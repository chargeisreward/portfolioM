"""新代码入库 service — LLM 判市场 + API 验名 + SecurityMaster + ApiCodeMap 建立。

流程（onboard_new_security）：
1. 查 SecurityMaster，若已存在则返回 {exists: True, ...}
2. LLM 判定市场 → market
3. 根据市场选 API：
   - CN/HK/US → crawlers/price_data.get_stock_info 拉名称
   - OF → akshare fund_open_fund_info_em 验证代码有效性
4. 调 services/code_map.transform_code 建立 api_code_map 映射
5. LLM 验证名称：verify_security_name_with_llm(security_name, name_from_api)
6. 创建 SecurityMaster 记录：
   - currency/market/asset_type/security_type/fund_type 由 _derive_* 推导
   - A股/港股指数/ETF → is_drillable=True
7. 返回入库结果

依赖：SecurityMaster, ApiCodeMap, services.llm_service, services.code_map,
      services.security_master_service._derive_*, crawlers.price_data.get_stock_info
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models import SecurityMaster
from services.llm_service import classify_market_with_llm, verify_security_name_with_llm
from services.security_master_service import (
    _derive_market, _derive_security_type, _derive_fund_type,
)
from services.code_map import transform_code, upsert_map

logger = logging.getLogger(__name__)


# 资产大类 → 是否可下钻 的判定表（A股/港股的指数、ETF 设为可下钻）
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
        asset_type 字符串（对应 AssetType 枚举的 value）
    """
    name_upper = (security_name or "").upper()
    code = (security_code or "").upper()

    if market == "OF":
        return "a_share_equity"  # 默认归类为 A 股联接基金（场外）
    if market == "CN":
        # A 股 ETF：6 位 + .SZ/.SH 后缀
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

    Args:
        security_code: OF 基金代码（含或不含 .OF 后缀）

    Returns:
        True 表示代码有效（akshare 返回非空数据），False 表示无效或拉取失败
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
    """根据市场调对应 API 拉取证券名称。

    Args:
        security_code: 证券代码
        market: CN / HK / US / OF

    Returns:
        (api_name, api_response_dict)。api_name 为 None 表示拉取失败或无名称。
    """
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
        # 场外基金：用 akshare 验证代码有效性，但基金名称通常不在 NAV df 中
        # 这里只验证代码能拉到数据，名称留给 LLM 验证
        valid = _verify_of_fund_via_akshare(security_code)
        if valid:
            return None, {"code": security_code, "source": "akshare", "valid": True}
        return None, None

    return None, None


def _ensure_api_code_maps(db: Session, security_code: str, market: str) -> None:
    """为新代码建立 api_code_map 映射（CN/HK/US → tencent_quote；OF → akshare_fund_nav）。

    Args:
        db: 数据库 session
        security_code: 证券代码
        market: CN / HK / US / OF
    """
    if market in ("CN", "HK", "US"):
        # 调 transform_code 触发自动建图（_default_transform + 惰性持久化）
        mapped = transform_code(security_code, "tencent_quote", db)
        if mapped and mapped != security_code:
            upsert_map(db, security_code, "tencent_quote", mapped, market=market,
                       note="auto-created by security_onboarding")
        # 也建一个 kline 映射
        mapped_kline = transform_code(security_code, "tencent_kline", db)
        if mapped_kline and mapped_kline != security_code:
            upsert_map(db, security_code, "tencent_kline", mapped_kline, market=market,
                       note="auto-created by security_onboarding")
    elif market == "OF":
        mapped = transform_code(security_code, "akshare_fund_nav", db)
        if mapped and mapped != security_code:
            upsert_map(db, security_code, "akshare_fund_nav", mapped, market=market,
                       note="auto-created by security_onboarding")


def onboard_new_security(db: Session, security_code: str, security_name: str,
                         context: str = "") -> dict:
    """新代码入库完整流程。

    Args:
        db: 数据库 session
        security_code: 证券代码
        security_name: 用户/LLM 提供的名称
        context: 交易记录上下文（用于 LLM 判定市场，可选）

    Returns:
        {
            "exists": bool,                  # 是否已在 SecurityMaster
            "security_code": str,
            "market": str | None,            # CN/HK/US/OF
            "name_from_api": str | None,     # API 拉取的名称
            "name_match": bool,              # LLM 名称验证结果
            "security_verified": bool,       # 综合判定（API 有效 + 名称匹配）
            "security_master": dict | None,  # 创建后的 SM 数据
            "api_response": dict | None,     # API 原始响应（调试用）
            "error": str | None,             # 错误信息（如有）
        }
    """
    result = {
        "exists": False,
        "security_code": security_code,
        "market": None,
        "name_from_api": None,
        "name_match": False,
        "security_verified": False,
        "security_master": None,
        "api_response": None,
        "error": None,
    }

    # Step 1: 查 SecurityMaster
    sm = db.query(SecurityMaster).filter(
        SecurityMaster.security_code == security_code
    ).first()
    if sm:
        result["exists"] = True
        result["market"] = sm.market
        result["security_master"] = {
            "security_code": sm.security_code,
            "security_name": sm.security_name,
            "market": sm.market,
            "asset_type": sm.asset_type,
            "is_drillable": sm.is_drillable,
        }
        return result

    # Step 2: LLM 判定市场
    market = classify_market_with_llm(security_code, security_name, context)
    if market is None:
        # LLM 不可用 → 用代码规则兜底
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
        # API 无名称（如 OF 基金）— 若 API 拉到数据则视为代码有效，名称留待用户确认
        name_match = False
    result["name_match"] = name_match

    # 综合判定：API 拉到数据 + (名称匹配 或 无 API 名称但代码有效)
    api_valid = bool(api_resp) and api_resp.get("source") != "none" and api_resp.get("valid", True)
    if api_name:
        result["security_verified"] = name_match
    else:
        # 无 API 名称时，以代码有效性为准
        result["security_verified"] = api_valid

    # Step 6: 创建 SecurityMaster
    asset_type = _derive_asset_type(security_code, market, security_name)
    security_type = _derive_security_type(asset_type)
    fund_type = _derive_fund_type(security_code, asset_type)
    # 货币推断：HK → HKD，US → USD，CN/OF → CNY
    currency = "HKD" if market == "HK" else ("USD" if market == "US" else "CNY")
    # 可下钻：A 股/港股市场的指数、ETF（基金类）
    is_drillable = asset_type in _DRILLABLE_ASSET_TYPES

    # 优先用 API 名称；API 无名称则用用户提供的名称
    final_name = api_name or security_name

    try:
        sm = SecurityMaster(
            security_code=security_code,
            security_name=final_name,
            currency=currency,
            asset_type=asset_type,
            security_type=security_type,
            fund_type=fund_type,
            market=market,
            is_drillable=is_drillable,
            note=f"auto-onboarded (match={name_match})",
        )
        db.add(sm)
        db.commit()
        db.refresh(sm)
        result["security_master"] = {
            "security_code": sm.security_code,
            "security_name": sm.security_name,
            "market": sm.market,
            "asset_type": sm.asset_type,
            "is_drillable": sm.is_drillable,
            "currency": sm.currency,
            "security_type": sm.security_type,
            "fund_type": sm.fund_type,
        }
    except Exception as e:
        db.rollback()
        result["error"] = f"创建 SecurityMaster 失败: {e}"
        logger.error("onboard_new_security: 创建 SM 失败 %s: %s", security_code, e)

    return result
