"""海外证券三源路由器 v2（hourly cron 主路径）— 接续 sub-project 3 的单源 service。

数据流：collect_codes (跨用户并集去重 + 当日跳过) → resolve_routes (三源决策 + LLM 兜底)
       → fetch_in_batches (腾讯批量 / Naver 逐个 / yfinance 逐个)
       → upsert (复用 overseas_financial_service) + 双写 StockInfoCache

约束：sub-project 3 的 overseas_financial_service.py + admin 手动 API 不动，本模块仅新增。
"""
from __future__ import annotations

import logging
from datetime import date

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