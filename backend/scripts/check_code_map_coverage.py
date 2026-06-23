"""check_code_map_coverage.py — 定时拉取任务前的代码映射覆盖率预检。

遍历三类证券池：
  - holdings  ：Holding.security_code（用户当前持仓）
  - watchlist ：Watchlist.code（关注清单）
  - drilled   ：FullHoldingSnapshot 当前业务日期的 distinct stock_code
                 （已下钻持仓 / 持仓基金穿透后的成分股）

对每个 code × 每个候选 api_strategy 调用 transform_code()：
  - 返回非 None 且 ≠ code_in   → 已映射 ✓
  - 返回 None                   → 该 API 不支持，需记录但不报错
                                    （例如 Tencent K 线对 .OF 基金本就返回 None）
  - 返回 == code_in             → 规则未命中，记为「未映射」✗

退出码：
  0  全部 OK（无未映射，且 missing_unmapped 为 0）
  1  发现未映射或脚本异常
  2  严重错误（无法连接 DB / 必需表缺失）

供 APScheduler 各 fetch_* 任务前置调用，亦可独立运行：
  python -m scripts.check_code_map_coverage [--strict] [--json] [--pool holdings|watchlist|drilled|all]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Iterable

# 候选 API 策略（与 backend/api_strategies.json 的 id 对齐）
DEFAULT_API_STRATEGIES = (
    "tencent_kline",
    "tencent_quote",
    "yfinance",
    "akshare_fund_nav",
    "akshare_etf_index",
)

# Tencent K 线对以下 code 本就不支持，返回 None 不算 missing。
# 这里列出会「合理不支持」的后缀 / 类型，避免假阳性。
_TENCENT_OPT_OUT_SUFFIXES = (".OF",)  # 场外基金走 akshare_fund_nav

# 已知「该 API 根本不支持此 code 类型」的组合 — 这些视为合理 unsupported，
# 不计为 missing。key = (api_strategy, code_suffix)
_KNOWN_UNSUPPORTED: set[tuple[str, str]] = {
    # yfinance 不支持场外基金 (.OF)
    ("yfinance", ".OF"),
    # akshare_etf_index 只做 A 股场内 ETF，不做港股 / 美股 / OF
    ("akshare_etf_index", ".OF"),
    ("akshare_etf_index", ".HK"),
    # akshare_fund_nav 只做 OF 场外基金
    ("akshare_fund_nav", ".HK"),
    ("akshare_fund_nav", ".SH"),
    ("akshare_fund_nav", ".SZ"),
}

# 「code_out == code_in 算 OK」的 API 集合 — 这些 API 用标准 ticker 不需要 transform。
# 例如 yfinance 接受 "GOOGL"、"159326.SZ"、"00005.HK" 原样传入。
_PASSTHROUGH_APIS = frozenset({"yfinance"})


@dataclass
class CoverageRow:
    code_in: str
    market: str | None
    api_strategy: str
    code_out: str | None  # None = API 不支持；str = 转换后
    status: str          # "mapped" | "unmapped" | "unsupported"

    def is_problem(self) -> bool:
        """是否构成「会拉取失败」的 missing"""
        return self.status == "unmapped"


@dataclass
class PoolReport:
    name: str
    total_codes: int
    rows: list[CoverageRow] = field(default_factory=list)

    @property
    def missing(self) -> list[CoverageRow]:
        return [r for r in self.rows if r.is_problem()]

    @property
    def mapped(self) -> int:
        return sum(1 for r in self.rows if r.status == "mapped")

    @property
    def unsupported(self) -> int:
        return sum(1 for r in self.rows if r.status == "unsupported")


def _market_of_code(code: str) -> str | None:
    """简单市场判断（与 services/code_map._market_of_code 保持一致）"""
    c = (code or "").upper().strip()
    if not c:
        return None
    if c.endswith(".OF"):
        return "OF"
    if c.endswith(".HK"):
        return "HK"
    if c.endswith(".SH") or c.endswith(".SZ"):
        return "CN"
    # 纯数字 → A/H 股
    if c.isdigit() and len(c) == 6:
        return "CN"
    if c.isdigit() and len(c) == 5:
        return "HK"
    return "US"


def _classify(code_in: str, api_strategy: str, code_out: str | None) -> str:
    """根据 (code_in, api_strategy, code_out) 判定 status。

    - "unsupported"：API 已知不支持此 code 类型（由调用方在 _is_known_unsupported 之前过滤）
    - "mapped"：code_out 是 API 能接受的格式（无论是 transform 后还是 passthrough）
    - "unmapped"：transform 规则没命中但 API 应该能处理 — 调用时多半会失败
    """
    if code_out is None:
        return "unsupported"
    if code_out == code_in:
        # 原样返回：passthrough API（yfinance）算 OK；其他 API 算 unmapped
        if api_strategy in _PASSTHROUGH_APIS:
            return "mapped"
        return "unmapped"
    return "mapped"


def _should_skip_tencent_unsupported(code_in: str, api_strategy: str, code_out: str | None) -> bool:
    """Tencent K 线 / Quote 对 .OF 基金本就返回 None，这是合理 unsupported，不算 missing"""
    if code_out is not None:
        return False
    if not api_strategy.startswith("tencent"):
        return False
    return any(code_in.endswith(s) for s in _TENCENT_OPT_OUT_SUFFIXES)


def _is_known_unsupported(code_in: str, api_strategy: str) -> bool:
    """在 _KNOWN_UNSUPPORTED 表里的 (api, code_suffix) 组合 → 视为合理 unsupported。

    港股 (.HK) / 美股 (无后缀 / 全字母) 也对 akshare_fund_nav / akshare_etf_index 不支持 —
    这两个 akshare 接口只处理 A 股 / OF 基金。
    """
    suf_candidates = _TENCENT_OPT_OUT_SUFFIXES + (".HK", ".SH", ".SZ")
    for suf in suf_candidates:
        if code_in.endswith(suf) and (api_strategy, suf) in _KNOWN_UNSUPPORTED:
            return True
    # akshare_fund_nav / akshare_etf_index 对无后缀 / 非纯数字的 code（美股）也不支持
    if api_strategy in ("akshare_fund_nav", "akshare_etf_index"):
        c = code_in.upper().strip()
        if not c.endswith((".OF", ".SH", ".SZ", ".HK")) and not c.isdigit():
            return True
    return False


def collect_holdings(db, api_strategies: Iterable[str]) -> list[str]:
    from models import Holding
    seen: set[str] = set()
    for h in db.query(Holding).all():
        c = h.security_code
        if c and c != "nan":
            seen.add(c)
    return sorted(seen)


def collect_watchlist(db, api_strategies: Iterable[str]) -> list[str]:
    from models import Watchlist
    seen: set[str] = set()
    for w in db.query(Watchlist).all():
        c = w.code
        if c and c != "nan":
            seen.add(c)
    return sorted(seen)


def collect_drilled(db, api_strategies: Iterable[str]) -> list[str]:
    from models import FullHoldingSnapshot
    from services.data_version import current_business_date
    from sqlalchemy import distinct
    biz = current_business_date()
    if not biz:
        return []
    seen: set[str] = set()
    rows = db.query(distinct(FullHoldingSnapshot.stock_code)).filter(
        FullHoldingSnapshot.as_of_date == biz,
        FullHoldingSnapshot.source_type.in_(("drilled_fund", "direct_stock")),
        FullHoldingSnapshot.stock_code.isnot(None),
    ).all()
    for (code,) in rows:
        if code and code != "nan":
            seen.add(code)
    return sorted(seen)


_POOL_COLLECTORS = {
    "holdings": collect_holdings,
    "watchlist": collect_watchlist,
    "drilled": collect_drilled,
}


def check_pool(db, pool_name: str, codes: list[str], api_strategies: list[str]) -> PoolReport:
    from services.code_map import transform_code

    report = PoolReport(name=pool_name, total_codes=len(codes))
    for code in codes:
        market = _market_of_code(code)
        for api in api_strategies:
            try:
                out = transform_code(code, api, db)
            except Exception as e:
                logging.warning("transform_code(%s, %s) raised: %s", code, api, e)
                out = None
            # 合理 unsupported：tencent 对 .OF 基金
            if _should_skip_tencent_unsupported(code, api, out):
                continue
            # 已知不支持：(api, code_suffix) 在 _KNOWN_UNSUPPORTED 表里
            # 此时即使 transform_code 返回了原 code，也按 unsupported 处理
            if _is_known_unsupported(code, api):
                # 把这些 rows 略过 — 它们不算 missing，也不计入统计
                continue
            status = _classify(code, api, out)
            report.rows.append(CoverageRow(
                code_in=code, market=market,
                api_strategy=api, code_out=out,
                status=status,
            ))
    return report


def run(pools: list[str], api_strategies: list[str] | None = None,
        db_factory=None, logger: logging.Logger | None = None) -> tuple[list[PoolReport], dict]:
    """主入口：返回 (reports, summary_dict)。
    db_factory: 可注入测试用 session 工厂；默认用 database.SessionLocal。
    """
    log = logger or logging.getLogger("check_code_map_coverage")
    if api_strategies is None:
        api_strategies = list(DEFAULT_API_STRATEGIES)

    if db_factory is None:
        from database import SessionLocal
        db_factory = SessionLocal

    db = db_factory()
    try:
        reports: list[PoolReport] = []
        for p in pools:
            collector = _POOL_COLLECTORS.get(p)
            if not collector:
                log.warning("未知池 %s，跳过", p)
                continue
            codes = collector(db, api_strategies)
            log.info("池 %s: %d 个 code", p, len(codes))
            reports.append(check_pool(db, p, codes, api_strategies))
    finally:
        db.close()

    summary = {
        "pools": [
            {
                "name": r.name,
                "total_codes": r.total_codes,
                "rows": len(r.rows),
                "mapped": r.mapped,
                "unsupported": r.unsupported,
                "missing": len(r.missing),
                "missing_examples": [
                    {"code": x.code_in, "api": x.api_strategy} for x in r.missing[:10]
                ],
            }
            for r in reports
        ],
        "total_missing": sum(len(r.missing) for r in reports),
    }
    return reports, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", default="all",
                        choices=("all", "holdings", "watchlist", "drilled"),
                        help="要检查的池")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式：合理 unsupported (.OF→tencent) 也算 missing")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--quiet", action="store_true", help="只打印 summary")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("check_code_map_coverage")

    pools = ["holdings", "watchlist", "drilled"] if args.pool == "all" else [args.pool]

    try:
        reports, summary = run(pools)
    except Exception as e:
        log.error("脚本异常: %s", e, exc_info=True)
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2

    if args.json or args.quiet:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for r in reports:
            print(f"\n=== 池 {r.name}: {r.total_codes} 个 code, "
                  f"{r.mapped} mapped, {r.unsupported} unsupported, "
                  f"{len(r.missing)} missing ===")
            for x in r.missing[:20]:
                print(f"  ✗ {x.code_in:20s} [{x.market or '-':2s}] {x.api_strategy:18s} → {x.code_out!r}")
            if len(r.missing) > 20:
                print(f"  ... ({len(r.missing) - 20} more)")
        print()
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    return 1 if summary["total_missing"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
