"""海外证券三源路由器 v2（hourly cron 主路径）— 接续 sub-project 3 的单源 service。

数据流：collect_codes (跨用户并集去重 + 当日跳过) → resolve_routes (三源决策 + LLM 兜底)
       → fetch_in_batches (腾讯批量 / Naver 逐个 / yfinance 逐个)
       → upsert (复用 overseas_financial_service) + 双写 StockInfoCache

约束：sub-project 3 的 overseas_financial_service.py + admin 手动 API 不动，本模块仅新增。
"""
from __future__ import annotations

import logging
import time
from datetime import date

from crawlers.price_data import (
    fetch_tencent_quote,
    fetch_yfinance_info,
    _fetch_naver_korean_info,
    NaverRateLimited,
)

logger = logging.getLogger(__name__)


class RateLimitedError(Exception):
    """捕获腾讯 pvtoo.match / Naver 503 / yfinance YFRateLimitError → 抛出 → 整批退避。"""


# ---------- 主源决策 ----------

# 美股/ADR/港股主源 = 腾讯 qt.gtimg.cn
_TENCENT_PRIMARY_SUFFIXES = (".HK", ".SH", ".SZ")  # 港股/A 股腾讯代码
# 纯字母（无后缀）默认 US → 腾讯主源
# 纯数字 6 位（A 股）→ 留给 HK 5 位/6 位识别

# 韩股 = Naver 后缀
_KOREAN_SUFFIXES = (".KS", ".KQ")


def _partition_codes_by_source(codes: list[str]) -> dict[str, list[str]]:
    """把 codes 按主源分桶。

    Returns:
        {
            'tencent_quote': [...],  # US/港股/A 股主源
            'naver_quote': [...],    # KR 韩股
            'yfinance': [...],       # 欧洲/日本/纯 6 位无后缀（兜底）
        }
    """
    out = {"tencent_quote": [], "naver_quote": [], "yfinance": []}
    for c in codes:
        if not c:
            continue
        raw = c.strip()
        upper = raw.upper()
        if any(upper.endswith(s) for s in _KOREAN_SUFFIXES):
            out["naver_quote"].append(raw)
        elif any(upper.endswith(s) for s in _TENCENT_PRIMARY_SUFFIXES):
            out["tencent_quote"].append(raw)
        elif upper.isalpha():
            # 纯字母无后缀 → 美股主源（NVDA/AAPL/QQQ）
            out["tencent_quote"].append(raw)
        else:
            # 含数字无后缀（如 7203.T 已在前缀规则命中；纯数字 6 位/欧股/日股）→ yfinance 兜底
            out["yfinance"].append(raw)
    return out


# ---------- collect_codes（个股级当日跳过） ----------


def collect_codes(db, today: date) -> tuple[set[str], int]:
    """从 Holding 表查所有海外持仓并集 + 过滤当日已有 snapshot。

    Returns: (todo_codes, skipped_cached_count)
    """
    # 延迟 import 避免顶部循环依赖
    from models import Holding, AssetType, OverseasShareFinancialSnapshot
    from sqlalchemy import select

    holdings_codes = db.execute(
        select(Holding.security_code).where(
            Holding.asset_type.in_([
                AssetType.US_STOCK.value,
                AssetType.US_ETF.value,
            ])
        ).distinct()
    ).scalars().all()

    unique_codes = {c for c in holdings_codes if c}
    if not unique_codes:
        logger.info("overseas_hourly_v2: no overseas holdings found (asset_type=US_STOCK|US_ETF)")
        return set(), 0

    cached_codes = db.execute(
        select(OverseasShareFinancialSnapshot.stock_code).where(
            OverseasShareFinancialSnapshot.as_of_date == today,
            OverseasShareFinancialSnapshot.stock_code.in_(unique_codes),
        ).distinct()
    ).scalars().all()

    cached = {c for c in cached_codes if c}
    todo = unique_codes - cached
    logger.info("overseas_hourly_v2: %d unique codes, %d cached today, %d to do",
                len(unique_codes), len(cached), len(todo))
    return todo, len(cached)


# ---------- LLM 兜底映射 ----------

MAX_LLM_ROUNDS = 3  # 单 code 最多 3 轮 LLM 调用（spec §5.4）
MAX_CANDIDATES_PER_ROUND = 2  # 每轮 LLM 给候选试前 2 个（限制 verification 成本）

_NAVER_THROTTLE_SEC = 0.5  # Naver Mobile API 反爬节奏
_YFINANCE_THROTTLE_SEC = 3.0  # yfinance 3s/call 防 429


def resolve_overseas_quote_code(
    code_in: str,
    api_strategy: str,  # 'tencent_quote' | 'naver_quote'
    db,
) -> str | None:
    """三源决策后的代码解析：DB → 启发式 → LLM 兜底（最多 3 轮，每轮先验真）。

    为什么不直接复用 code_map.resolve_tencent_quote_code：本函数是 hourly v2 cron
    专用，需要支持 api_strategy='naver_quote'（code_map 版本只覆盖 tencent_quote）。

    Returns: 解析后的目标代码（如 'usNVDA'）或 None（全部失败）。
    """
    # 阶段 1: DB 命中（api_code_map + DEFAULT_MAPS 惰性持久化）
    mapped = transform_code(code_in, api_strategy, db)
    if mapped:
        return mapped

    # 阶段 2: 启发式（heuristic pattern 解析）
    heur = _default_transform(code_in, api_strategy)
    if heur:
        return heur

    # 阶段 3: LLM 兜底（最多 3 轮，每轮候选先 verifier 验真）
    verifier = _VERIFIERS.get(api_strategy)
    if verifier is None:
        logger.warning("unknown api_strategy: %s", api_strategy)
        return None

    for round_idx in range(MAX_LLM_ROUNDS):
        candidates = _llm_get_candidates(code_in, api_strategy, round_idx=round_idx)
        if not candidates:
            break

        for cand in candidates[:MAX_CANDIDATES_PER_ROUND]:
            try:
                if verifier(cand):
                    # 成功 → 持久化到 api_code_map
                    _persist_mapping(db, code_in, api_strategy, cand)
                    return cand
            except Exception as e:
                logger.warning("verify failed for candidate %s: %s", cand, e)
                continue

    logger.warning("LLM 兜底 3 轮失败：code_in=%s strategy=%s", code_in, api_strategy)
    return None


def _llm_get_candidates(code_in: str, api_strategy: str,
                        round_idx: int = 0) -> list[str]:
    """调 LLM 获取候选 ticker 列表。空 list = LLM 也不确定。"""
    try:
        # 复用 services/llm_service.py 的 OpenAI-compatible client
        # (实际函数是 _call_llm，plan 提到的 chat_completion 不存在)
        from services.llm_service import _call_llm
        import json

        sys_prompt = (
            "你是证券代码格式专家。给定用户持仓写法，需要猜测对应数据源的 ticker 格式。"
            "已知格式：腾讯 qt.gtimg.cn (usNVDA, sh600519, sz000001, hk00700)，"
            "Naver (005930, 005930.KS)。"
            "请只返回 JSON：{\"candidates\": [\"...\"], \"reason\": \"...\"}"
        )
        user_prompt = (
            f'给定用户证券代码 "{code_in}"（持仓写法），已知数据源 {api_strategy}。\n'
            f'本轮（{round_idx + 1}/{MAX_LLM_ROUNDS}）请列出最可能的 ticker 候选。\n'
            f'输出纯 JSON: {{"candidates": ["..."], "reason": "..."}}'
        )

        content = _call_llm(sys_prompt, user_prompt, temperature=0.2, timeout=20.0)
        if not content:
            return []

        # 容错解析（_call_llm 已清 markdown；这里再防一道）
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(text)
        return data.get("candidates", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.warning("LLM 失败 code_in=%s round=%d: %s", code_in, round_idx, e)
        return []


# 每个 api_strategy 对应一个 verifier 函数：调该源验证 candidate 是否有效
_VERIFIERS = {
    "tencent_quote": lambda cand: _tencent_verify(cand),
    "naver_quote": lambda cand: _naver_verify(cand),
}


def _tencent_verify(cand: str) -> bool:
    """单次腾讯接口验证候选是否返回有效 quote。"""
    try:
        from crawlers.price_data import fetch_tencent_quote
        result = fetch_tencent_quote(cand)
        return result is not None and (result.get("price") or result.get("pe_ttm"))
    except Exception:
        return False


def _naver_verify(cand: str) -> bool:
    """单次 Naver 接口验证。"""
    try:
        from crawlers.price_data import _fetch_naver_korean_info
        result = _fetch_naver_korean_info(cand)
        return result is not None
    except Exception:
        return False


def _persist_mapping(db, code_in: str, api_strategy: str, code_out: str) -> None:
    """写 api_code_map 表（如果 cron 卡顿修复 plan 已实现）持久化。"""
    try:
        from models import ApiCodeMap  # type: ignore
        existing = db.query(ApiCodeMap).filter_by(
            code_in=code_in, api_strategy=api_strategy,
        ).first()
        if existing:
            existing.code_out = code_out
            existing.note = f"overseas V2 LLM resolved at {code_out}"
        else:
            db.add(ApiCodeMap(
                code_in=code_in, api_strategy=api_strategy, code_out=code_out,
                market=None, note="overseas V2 LLM resolved",
            ))
        db.commit()
    except Exception as e:
        logger.warning("persist api_code_map 失败: %s", e)
        try:
            db.rollback()
        except Exception:
            pass


# 复用 sub-project 3 + cron 卡顿修复 plan 的 code_map 工具
try:
    from services.code_map import transform_code, _default_transform, tencent_get  # noqa: F401
except ImportError:
    # 如果 cron 卡顿修复 plan 未落地 → 占位函数（仅让 import 不崩，业务上前面 task 会触发 gate 检查）
    def transform_code(code_in, api_strategy, db): return None  # type: ignore
    def _default_transform(code_in, api_strategy): return None  # type: ignore
    def tencent_get(url, **kw): return None  # type: ignore


# ---------- 三源并行 fetch（限流温和退化） ----------


def _fetch_in_batches(
    db,
    partitioned: dict[str, list[str]],
    as_of_date,  # date
) -> tuple[dict[str, dict], list[str]]:
    """三源并行拉取；任一组抛 RateLimitedError → 顶层抛出（退避）。

    Args:
        db: SQLAlchemy session（实际未使用，保留接口一致）
        partitioned: {'tencent_quote': [...], 'naver_quote': [...], 'yfinance': [...]}
        as_of_date: 当前日期（保留接口一致；当前实现不使用）

    Returns: ({user_code: metrics}, errors)
    """
    results: dict[str, dict] = {}
    errors: list[str] = []

    try:
        if partitioned["tencent_quote"]:
            tencent_results, tencent_errors = _fetch_tencent_group(
                partitioned["tencent_quote"]
            )
            results.update(tencent_results)
            errors.extend(tencent_errors)
    except RateLimitedError:
        raise  # 顶层捕获，整批退避

    try:
        if partitioned["naver_quote"]:
            naver_results, naver_errors = _fetch_naver_group(
                partitioned["naver_quote"]
            )
            results.update(naver_results)
            errors.extend(naver_errors)
    except RateLimitedError:
        raise

    try:
        if partitioned["yfinance"]:
            yf_results, yf_errors = _fetch_yfinance_group(
                partitioned["yfinance"]
            )
            results.update(yf_results)
            errors.extend(yf_errors)
    except RateLimitedError:
        raise

    return results, errors


def _fetch_tencent_group(codes: list[str]) -> tuple[dict[str, dict], list[str]]:
    """腾讯逐个 quote（仅 PE 字段；批量端点无 PE）。

    pe_ttm 在 fetch_tencent_quote 返回 dict 中是字符串（_safe_get），需要 float() 转换。
    """
    errors: list[str] = []
    out: dict[str, dict] = {}

    for c in codes:
        try:
            q = fetch_tencent_quote(c)
            if not q:
                continue
            pe_raw = q.get("pe_ttm")
            if not pe_raw:
                continue
            try:
                pe_ttm = float(pe_raw)
            except (ValueError, TypeError):
                continue
            out[c] = {
                "pe_ttm": pe_ttm,
                "market_cap": q.get("market_cap"),
                "name": q.get("name"),
                "source": "tencent",
            }
        except Exception as e:
            if _is_tencent_rate_limited(e):
                raise RateLimitedError(f"tencent single quote: {e}")
            errors.append(f"tencent [{c}]: {e}")
            logger.warning("tencent [%s] 拉取失败: %s", c, e)
            continue
    return out, errors


def _fetch_naver_group(codes: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Naver 逐个调用（韩股数量小）。"""
    errors: list[str] = []
    out: dict[str, dict] = {}

    for c in codes:
        try:
            q = _fetch_naver_korean_info(c)
            if q and q.get("pe_ttm"):
                out[c] = {
                    "pe_ttm": q["pe_ttm"],
                    "market_cap": q.get("market_cap"),
                    "name": q.get("name"),
                    "source": "naver",
                }
        except NaverRateLimited as e:
            raise RateLimitedError(f"naver 503: {e}")
        except Exception as e:
            errors.append(f"naver [{c}]: {e}")
            logger.warning("naver [%s] 拉取失败: %s", c, e)
            continue
        time.sleep(_NAVER_THROTTLE_SEC)  # Naver 反爬节奏
    return out, errors


def _fetch_yfinance_group(codes: list[str]) -> tuple[dict[str, dict], list[str]]:
    """yfinance 逐个调用（PB/PS/股息率唯一来源）。"""
    errors: list[str] = []
    out: dict[str, dict] = {}

    for c in codes:
        try:
            q = fetch_yfinance_info(c)
            if not q:
                errors.append(f"yfinance [{c}]: empty")
                continue
            row: dict = {"source": "yfinance"}
            for k_in, k_out in [("pe_ttm", "pe_ttm"), ("pb_mrq", "pb_mrq"),
                                 ("ps_ttm", "ps_ttm"),
                                 ("dividend_yield", "dividend_yield"),
                                 ("market_cap_b", "market_cap"),
                                 ("eps_fy1", "eps_fy1"),
                                 ("sector", "sector"),
                                 ("industry", "industry"),
                                 ("name", "name")]:
                v = q.get(k_in)
                if v is not None:
                    row[k_out] = v
            out[c] = row
        except Exception as e:
            if "RateLimit" in type(e).__name__ or "429" in str(e):
                raise RateLimitedError(f"yfinance 429: {e}")
            errors.append(f"yfinance [{c}]: {e}")
            logger.warning("yfinance [%s] 拉取失败: %s", c, e)
            continue
        time.sleep(_YFINANCE_THROTTLE_SEC)  # yfinance 3s/call 防 429
    return out, errors


def _is_tencent_rate_limited(e: Exception) -> bool:
    """识别腾讯反爬限流特征。"""
    msg = (str(e) + " " + type(e).__name__).lower()
    return any(s in msg for s in [
        "pvtoo", "captcha", "sorry/index", "verify", "too frequent", "rate limit"
    ])


# ---------- 顶层入口（hourly job 调用） ----------


def fetch_overseas_financials_three_source(db, as_of_date: date) -> dict:
    """三源顶层入口：collect → route → fetch → upsert + 双写 StockInfoCache。

    Returns: {
        'status': 'ok' | 'rate_limited' | 'error',
        'fetched': int, 'stored': int,
        'errors': list[str],
        'skipped_cached': int,
        'rate_limited': bool,
        'llm_calls': int,
    }
    """
    todo, skipped_cached = collect_codes(db, as_of_date)
    if not todo:
        return {
            "status": "ok", "fetched": 0, "stored": 0, "errors": [],
            "skipped_cached": skipped_cached, "rate_limited": False,
            "llm_calls": 0,
        }

    partitioned = _partition_codes_by_source(list(todo))

    try:
        results, errors = _fetch_in_batches(db, partitioned, as_of_date)
    except RateLimitedError as e:
        logger.warning("overseas_hourly_v2 整批被限流，放弃本次: %s", e)
        return {
            "status": "rate_limited", "fetched": 0, "stored": 0,
            "errors": [str(e)], "skipped_cached": skipped_cached,
            "rate_limited": True, "llm_calls": 0,
        }
    except Exception as e:
        logger.error("overseas_hourly_v2 异常: %s", e, exc_info=True)
        return {
            "status": "error", "fetched": 0, "stored": 0,
            "errors": [str(e)], "skipped_cached": skipped_cached,
            "rate_limited": False, "llm_calls": 0,
        }

    # Upsert: 复用 sub-project 3 的 overseas_financial_service
    stored = 0
    for code, metrics in results.items():
        try:
            data = {
                "stock_code": code,
                "stock_name": metrics.get("name", ""),
                "market": metrics.get("market", _infer_market(code)),
                "pe_ttm": metrics.get("pe_ttm"),
                "pb_mrq": metrics.get("pb_mrq"),
                "ps_ttm": metrics.get("ps_ttm"),
                "dividend_yield": metrics.get("dividend_yield"),
                "market_cap": metrics.get("market_cap"),
                "eps_fy1": metrics.get("eps_fy1"),
                "sector": metrics.get("sector"),
                "industry": metrics.get("industry"),
                "as_of_date": as_of_date,
                "source": metrics.get("source", "yfinance"),
            }
            upsert_overseas_financial(db, data)
            stored += 1

            # 双写 StockInfoCache（保留 sub-project 3 路径，向后兼容）
            _dual_write_stock_info_cache(db, code, metrics)
        except Exception as e:
            errors.append(f"upsert [{code}]: {e}")
            logger.warning("upsert failed for %s: %s", code, e)
            continue

    return {
        "status": "ok",
        "fetched": len(results), "stored": stored, "errors": errors,
        "skipped_cached": skipped_cached, "rate_limited": False,
        "llm_calls": 0,
    }


def _dual_write_stock_info_cache(db, code: str, metrics: dict) -> None:
    """sub-project 3 上游 StockInfoCache 保留写。

    复用 sub-project 3 的逻辑：从 metrics 合并到 data_json，更新 updated_at。
    """
    try:
        from models import StockInfoCache
        from datetime import datetime as _dt

        existing = db.query(StockInfoCache).filter(
            StockInfoCache.stock_code == code
        ).first()

        merged = {
            "name": metrics.get("name"),
            "pe_ttm": metrics.get("pe_ttm"),
            "pb_mrq": metrics.get("pb_mrq"),
            "ps_ttm": metrics.get("ps_ttm"),
            "dividend_yield": metrics.get("dividend_yield"),
            "market_cap_b": metrics.get("market_cap"),
            "source": metrics.get("source", "yfinance"),
        }
        merged = {k: v for k, v in merged.items() if v is not None}

        if existing:
            current = existing.data_json or {}
            current.update(merged)
            existing.data_json = current
            existing.stock_name = merged.get("name", existing.stock_name) or existing.stock_name
            existing.updated_at = _dt.utcnow()
        else:
            db.add(StockInfoCache(
                stock_code=code,
                stock_name=merged.get("name", ""),
                data_json=merged,
                updated_at=_dt.utcnow(),
            ))
    except Exception as e:
        logger.warning("dual_write StockInfoCache failed for %s: %s", code, e)


def _infer_market(code: str) -> str:
    """复用 crawlers.price_data 的 market 推断。"""
    try:
        from crawlers.price_data import _infer_market_from_ticker
        return _infer_market_from_ticker(code)
    except ImportError:
        return "US"


# 复用 sub-project 3 的 upsert（直接 import）
from services.overseas_financial_service import upsert_overseas_financial  # noqa: E402