"""新代码入库 service — LLM 判市场 + API 验名 + 新主表 (StockMaster/FundMaster) + ApiCodeMap 建立。

流程 (onboard_new_security):
1. 查 新三表 (stock_master / fund_master / index_master),若已存在则返回 {exists: True, ...}
2. LLM 判定市场 → market
3. 根据市场选 API:
   - CN/HK/US → crawlers/price_data.get_stock_info 拉名称
   - OF → akshare fund_open_fund_info_em 验证代码有效性
4. 调 services.code_map.transform_code 建立 api_code_map 映射
5. LLM 验证名称:verify_security_name_with_llm(security_name, name_from_api)
6. 写入新主表:
   - us_stock → StockMaster
   - 其余 (基金/ETF/QDII/黄金/债券/指数) → FundMaster 或 IndexMaster
   - currency/market/asset_type/fund_type 由 _derive_* 推导
   - A股/港股指数/ETF → is_drillable=True
7. 返回入库结果

迁移说明 (2026-07-02):
- 旧版本写入 SecurityMaster
- 新版本写 StockMaster 或 FundMaster (根据 asset_type)
- SecurityMaster 已重命名为 security_master_legacy,冻结只读
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from services.llm_service import (
    classify_market_with_llm, verify_security_name_with_llm, verify_security_with_llm,
)
from services.security_master_service import (
    _derive_market, _derive_security_type, _derive_fund_type,
)
from services.code_map import transform_code, upsert_map
from services.security_lookup import (
    get_security_view, exists_in_new_tables,
    _derive_target_table, _to_target_kwargs,
)

logger = logging.getLogger(__name__)


# 资产大类 → 是否可下钻 的判定表 (A股/港股的指数、ETF 设为可下钻)
_DRILLABLE_ASSET_TYPES = frozenset({
    "a_share_equity",   # A股联接基金（跟踪指数）
    "a_share_etf",      # A股ETF
    "hk_equity",        # 港股基金（含 ETF/指数）
})


def _derive_asset_type(security_code: str, market: str, security_name: str = "") -> str:
    """根据代码 + 市场 + 名称推断 asset_type。

    Args:
        security_code: 证券代码
        market: CN / HK / US / OF
        security_name: 证券名称（含 ETF/指数/基金 等关键词时用于判定）

    Returns:
        asset_type 字符串
    """
    name_upper = (security_name or "").upper()
    code = (security_code or "").upper()

    if market == "OF":
        return "a_share_equity"  # 默认归类为 A 股联接基金（场外）
    if market == "CN":
        if ".SH" in code or ".SZ" in code:
            return "a_share_etf"
        return "a_share_equity"
    if market == "HK":
        return "hk_equity"
    if market == "US":
        if "ETF" in name_upper or code in ("QQQ", "SPY", "VOO", "VTI"):
            return "us_etf"
        return "us_stock"
    return "a_share_equity"


def _verify_of_fund_via_akshare(security_code: str) -> bool:
    """通过 akshare 验证 OF 基金代码有效性（能否拉到净值数据）。

    抽成独立函数便于测试 mock。
    """
    try:
        import akshare as ak
        code_raw = security_code.replace(".OF", "")
        df = ak.fund_open_fund_info_em(symbol=code_raw, indicator="单位净值走势")
        return df is not None and len(df) > 0
    except Exception as e:
        logger.warning("_verify_of_fund_via_akshare: %s 失败: %s", security_code, e)
        return False


def _fetch_name_from_api(security_code: str, market: str) -> tuple[str | None, dict | None]:
    """根据市场调对应 API 拉取证券名称。"""
    if market in ("CN", "HK", "US"):
        try:
            from crawlers.price_data import get_stock_info
            info = get_stock_info(security_code, timeout_sec=5)
            if info and info.get("source") != "none":
                return info.get("name"), info
        except Exception as e:
            logger.warning("_fetch_name_from_api: tencent/%s 失败: %s", market, e)
        return None, None

    if market == "OF":
        valid = _verify_of_fund_via_akshare(security_code)
        if valid:
            return None, {"code": security_code, "source": "akshare", "valid": True}
        return None, None

    return None, None


def _ensure_api_code_maps(db: Session, security_code: str, market: str) -> None:
    """为新代码建立 api_code_map 映射 (CN/HK/US → tencent_quote；OF → akshare_fund_nav)。"""
    if market in ("CN", "HK", "US"):
        mapped = transform_code(security_code, "tencent_quote", db)
        if mapped and mapped != security_code:
            upsert_map(db, security_code, "tencent_quote", mapped, market=market,
                       note="auto-created by security_onboarding")
        mapped_kline = transform_code(security_code, "tencent_kline", db)
        if mapped_kline and mapped_kline != security_code:
            upsert_map(db, security_code, "tencent_kline", mapped_kline, market=market,
                       note="auto-created by security_onboarding")
    elif market == "OF":
        mapped = transform_code(security_code, "akshare_fund_nav", db)
        if mapped and mapped != security_code:
            upsert_map(db, security_code, "akshare_fund_nav", mapped, market=market,
                       note="auto-created by security_onboarding")


def _upsert_to_new_table(db: Session, code: str, name: str, asset_type: str,
                          market: str, is_drillable: bool) -> dict | None:
    """写入新主表 (StockMaster / FundMaster / IndexMaster)。

    Returns:
        dict with security_code/security_name/market/asset_type/currency/is_drillable,
        or None on failure.
    """
    model, code_field = _derive_target_table(asset_type)
    currency = "HKD" if market == "HK" else ("USD" if market == "US" else "CNY")

    # 查已存在 (避免 unique constraint 冲突)
    existing = db.query(model).filter(
        getattr(model, code_field) == code
    ).first()
    if existing:
        # 已存在 — 增量更新名称(若提供)
        if name and getattr(existing, "fund_name" if model.__name__ == "FundMaster" else
                            "stock_name" if model.__name__ == "StockMaster" else
                            "index_name", None) != name:
            if model.__name__ == "FundMaster":
                existing.fund_name = name
            elif model.__name__ == "StockMaster":
                existing.stock_name = name
            else:
                existing.index_name = name
        return {
            "security_code": code,
            "security_name": getattr(existing, "fund_name" if model.__name__ == "FundMaster"
                                     else "stock_name" if model.__name__ == "StockMaster"
                                     else "index_name"),
            "market": market,
            "asset_type": existing.asset_type if hasattr(existing, "asset_type") else asset_type,
            "currency": getattr(existing, "currency", currency) or currency,
            "is_drillable": bool(existing.is_drillable),
        }

    # 新建
    view = {
        "security_code": code,
        "security_name": name or code,
        "asset_type": asset_type,
        "currency": currency,
        "exchange": None,
        "fund_type": None,
        "is_drillable": is_drillable,
        "benchmark_formula": None,
        "category": None,
        "source": "manual",
        "is_active": True,
        "is_listed": True,
        "note": None,
    }
    _, kwargs = _to_target_kwargs(asset_type, view)
    new_row = model(**kwargs)
    db.add(new_row)
    db.flush()
    return {
        "security_code": code,
        "security_name": name or code,
        "market": market,
        "asset_type": asset_type,
        "currency": currency,
        "is_drillable": is_drillable,
    }


def onboard_new_security(db: Session, security_code: str, security_name: str,
                         context: str = "") -> dict:
    """新代码入库完整流程。

    Args:
        db: 数据库 session
        security_code: 证券代码
        security_name: 用户/LLM 提供的名称
        context: 交易记录上下文（用于 LLM 判定市场，可选）

    Returns:
        dict with exists / security_code / market / name_from_api / name_match /
             security_verified / security_master / api_response / error
    """
    result = {
        "exists": False,
        "security_code": security_code,
        "market": None,
        "name_from_api": None,
        "name_match": False,
        "security_verified": False,
        "security_master": None,  # 保留字段名(兼容前端)
        "api_response": None,
        "error": None,
    }

    # Step 1: 查 新主表 (legacy 已冻结,不再写入 — 但读取仍走 unified lookup)
    existing_view = get_security_view(db, security_code)
    if existing_view:
        result["exists"] = True
        result["market"] = existing_view.get("market") or _derive_market(security_code)
        result["security_master"] = {
            "security_code": existing_view["security_code"],
            "security_name": existing_view["security_name"],
            "market": result["market"],
            "asset_type": existing_view.get("asset_type"),
            "is_drillable": existing_view.get("is_drillable", False),
            "currency": existing_view.get("currency"),
            "security_type": existing_view.get("security_type"),
            "fund_type": existing_view.get("fund_type"),
        }
        return result

    # Step 2: LLM 判定市场
    market = classify_market_with_llm(security_code, security_name, context)
    if market is None:
        market = _derive_market(security_code)
        logger.info("LLM 判市场失败，用代码规则兜底: %s → %s", security_code, market)
    result["market"] = market

    # Step 3 + 4: 调 API 拉名称 + 建立 api_code_map
    try:
        _ensure_api_code_maps(db, security_code, market)
    except Exception as e:
        logger.warning("建立 api_code_map 失败: %s", e)

    api_name, api_resp = _fetch_name_from_api(security_code, market)
    result["name_from_api"] = api_name
    result["api_response"] = api_resp

    # Step 5: LLM 验证名称
    if api_name:
        name_match = verify_security_name_with_llm(security_name, api_name)
    else:
        name_match = False
    result["name_match"] = name_match

    api_valid = bool(api_resp) and api_resp.get("source") != "none" and api_resp.get("valid", True)
    if api_name:
        result["security_verified"] = name_match
    else:
        result["security_verified"] = api_valid

    # Step 6: 写入新主表
    asset_type = _derive_asset_type(security_code, market, security_name)
    security_type = _derive_security_type(asset_type)
    fund_type = _derive_fund_type(security_code, asset_type)
    currency = "HKD" if market == "HK" else ("USD" if market == "US" else "CNY")
    is_drillable = asset_type in _DRILLABLE_ASSET_TYPES

    final_name = api_name or security_name

    try:
        sm_data = _upsert_to_new_table(
            db, security_code, final_name, asset_type, market, is_drillable
        )
        if sm_data:
            db.commit()
            result["security_master"] = sm_data
        else:
            result["error"] = "写入新主表返回空 (内部异常)"
    except Exception as e:
        db.rollback()
        result["error"] = f"创建新主表记录失败: {e}"
        logger.error("onboard_new_security: 写入新主表失败 %s: %s", security_code, e)

    return result


def verify_security_for_confirm(db: Session, security_code: str,
                                security_name: str) -> dict:
    """confirm 阶段证券校验 + 新证券入库。

    流程：
    1. 查新三表 (走 unified lookup):
       - 存在 → 调 verify_security_with_llm(code, name, view.security_name) 校验
       - 不存在 → 调 onboard_new_security 拉取数据构建主数据 + api_code_map
    2. onboard 后再次查新表,调 verify_security_with_llm 校验
    3. 返回校验结果
    """
    result = {
        "verified": False,
        "reason": "",
        "security_code": security_code,
        "security_name": security_name,
    }

    if not security_code:
        result["reason"] = "证券代码为空"
        return result

    # Step 1: 查新主表
    view = get_security_view(db, security_code)

    if not view:
        # 新三表 + legacy 均无 → onboard 新证券
        try:
            onboard_result = onboard_new_security(
                db, security_code, security_name, context="trades/confirm"
            )
            if onboard_result.get("error"):
                result["reason"] = f"入库失败: {onboard_result['error']}"
                return result
            # onboard 后重新查 unified view
            view = get_security_view(db, security_code)
        except Exception as e:
            db.rollback()
            result["reason"] = f"入库异常: {e}"
            logger.error("verify_security_for_confirm: onboard 异常 %s: %s", security_code, e)
            return result

    # Step 2: 用 verify_security_with_llm 校验
    sm_name = view.get("security_name") if view else None
    verify = verify_security_with_llm(security_code, security_name, sm_name)
    result["verified"] = verify["verified"]
    result["reason"] = verify["reason"]
    if view:
        result["security_name"] = view.get("security_name") or security_name
    return result