"""APScheduler 定时任务调度器

负责三类定时任务：
1. 实时行情抓取（交易时段每15分钟）
2. 财务基本面数据更新（每日7:00/19:00）
3. 行业/爬虫数据更新（每日6:00/20:00）
"""
import contextvars
import functools
import logging
import time as _time_mod
from datetime import datetime, date, time

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Holding, PriceCache, StockInfoCache, AssetType
from services.data_pull_task_service import record_task_start, record_task_finish

logger = logging.getLogger(__name__)

scheduler: BackgroundScheduler | None = None

# ---------- 运行状态跟踪 ----------
# 给前端 /api/scheduler/status 使用：每个 job_id 最近一次执行的元数据。
# 通过 track_run() 装饰器写入；手动触发也会记录（trigger_job 调用 _JOB_DISPATCH 中的 wrapper）。
_JOB_LAST_RUN: dict[str, dict] = {}

# 触发者上下文：APScheduler 自动触发为 "scheduler"，手动触发为 "manual" / "manual:<user_id>"
# track_run 据此判断是否由自己记录 DataPullTask（自动触发时记录，手动触发时由 trigger_job 记录）
_triggered_by_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "scheduler_triggered_by", default="scheduler"
)


def track_run(job_id: str):
    """装饰器：把函数的执行结果 / 异常 / 耗时写入 _JOB_LAST_RUN[job_id]。

    APScheduler 调用与 /api/scheduler/trigger 手动调用都走包装后的函数，
    因此 last_run_at 是「真实最近一次执行」而非「最近一次 cron 触发」。

    额外职责（Task 7）：当 triggered_by 上下文为 "scheduler"（APScheduler 自动触发）时，
    同步写入 DataPullTask 表记录任务历史。手动触发（trigger_job）时由 trigger_job
    负责记录，此处跳过以避免重复。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = _time_mod.time()
            run_at = datetime.now().isoformat(timespec="seconds")
            triggered_by = _triggered_by_ctx.get()

            # 仅 APScheduler 自动触发时记录 DataPullTask（手动触发由 trigger_job 记录）
            task_id = None
            if triggered_by == "scheduler":
                task_id = _try_record_start(job_id, triggered_by)

            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                _JOB_LAST_RUN[job_id] = {
                    "run_at": run_at,
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}"[:300],
                    "result": None,
                    "duration_ms": int((_time_mod.time() - t0) * 1000),
                }
                if task_id is not None:
                    _try_record_finish(task_id, "FAILED", error_message=str(e)[:500])
                raise
            _JOB_LAST_RUN[job_id] = {
                "run_at": run_at,
                "status": "ok",
                "error": None,
                "result": result if isinstance(result, dict)
                           else {"value": str(result)[:200] if result is not None else None},
                "duration_ms": int((_time_mod.time() - t0) * 1000),
            }
            if task_id is not None:
                _try_record_finish(task_id, "SUCCESS", records_pulled=_extract_record_count(result))
            return result
        return wrapper
    return deco


def _extract_record_count(result) -> int:
    """从 job 返回值中提取记录数（用于 records_pulled 字段）。"""
    if result is None:
        return 0
    if isinstance(result, (list, tuple)):
        return len(result)
    if isinstance(result, int):
        return result
    if isinstance(result, dict):
        # 常见字段：updated / written / filled_total / records / stocks_checked / count
        for key in ("updated", "written", "filled_total", "records", "stocks_checked", "count"):
            val = result.get(key)
            if isinstance(val, int):
                return val
        return 0
    return 0


def _try_record_start(job_id: str, triggered_by: str) -> int | None:
    """尝试记录任务开始（使用独立 db 会话，失败不阻塞 job 执行）。返回 task_id 或 None。"""
    rec_db = SessionLocal()
    try:
        task = record_task_start(rec_db, job_id, job_id, triggered_by)
        return task.get("id")
    except Exception as e:
        logger.warning("record_task_start 失败 (job=%s): %s", job_id, e)
        return None
    finally:
        rec_db.close()


def _try_record_finish(task_id: int, status: str, records_pulled: int = 0, error_message: str | None = None) -> None:
    """尝试记录任务结束（使用独立 db 会话，失败不阻塞）。"""
    rec_db = SessionLocal()
    try:
        record_task_finish(rec_db, task_id, status, records_pulled=records_pulled, error_message=error_message)
    except Exception as e:
        logger.warning("record_task_finish 失败 (task_id=%s): %s", task_id, e)
    finally:
        rec_db.close()


# ---------- 工具函数 ----------

def _is_a_share_trading_hours(now: datetime) -> bool:
    """判断当前是否处于A股交易时段（9:30-15:00 CST）"""
    t = now.time()
    return time(9, 30) <= t <= time(15, 0)


def _is_us_trading_hours(now: datetime) -> bool:
    """判断当前是否处于美股交易时段（21:30-次日4:00 CST）

    美股交易时间按北京时间换算：
    夏令时 21:30 - 次日 4:00
    冬令时 22:30 - 次日 5:00
    这里统一使用 21:30 - 次日 4:00 作为简化判断。
    """
    t = now.time()
    return t >= time(21, 30) or t <= time(4, 0)


def _is_trading_hours(now: datetime) -> bool:
    """判断当前是否处于任一市场交易时段"""
    return _is_a_share_trading_hours(now) or _is_us_trading_hours(now)


# ---------- 任务1：实时行情抓取 ----------

@track_run("realtime_prices")
def job_fetch_realtime_prices(force: bool = False, user_id: int | None = None):
    """每15分钟执行：抓取所有（或指定 user 的）持仓最新价格并更新缓存。
    交易时段（A股+美股）每15分钟；非交易时段只拉最近1天价。
    force=True 时强制全量拉（手动触发用）

    多用户升级：user_id=None 遍历所有 user 的持仓（保持原行为 — 价格共享）。
    """
    now = datetime.now()
    in_session = _is_trading_hours(now)

    db: Session = SessionLocal()
    try:
        from crawlers.price_data import fetch_tencent_quote
        from crawlers.exchange_rates import get_rate
        from services.importer import _fetch_fund_nav
        from services.trading_calendar import is_any_market_open_today
        from models import User

        # Pre-flight：持仓代码映射覆盖率（不阻塞，记录到 last_result）
        preflight = _run_code_map_preflight(db, pools=("holdings",))

        holdings_q = db.query(Holding)
        if user_id is not None:
            holdings_q = holdings_q.filter(Holding.user_id == user_id)
        holdings = holdings_q.all()
        if not holdings:
            logger.info("无持仓记录 (user_id=%s)，跳过行情抓取", user_id)
            return {"skipped": "no_holdings", "preflight": preflight, "user_id": user_id}

        today = date.today()
        # 日历门控：今日无任何市场开市（CN/HK/US）→ 跳过（force 强制仍跑）
        if not force:
            try:
                if not is_any_market_open_today(db):
                    logger.info("今日全市场休市（日历），跳过实时拉取")
                    return {"skipped": "all_markets_closed", "preflight": preflight}
            except Exception as e:
                logger.warning("日历门控失败，继续执行: %s", e)

        updated = 0

        for h in holdings:
            try:
                price = None
                code = h.security_code
                quote_info = None

                # 美股/美股ETF：通过腾讯财经API获取实时行情
                if h.asset_type in (AssetType.US_STOCK.value, AssetType.US_ETF.value):
                    quote_info = fetch_tencent_quote(code)
                    if quote_info:
                        price = quote_info.get("price")

                # 场外基金（.OF后缀）：通过akshare获取最新净值
                if not price and code.endswith(".OF"):
                    fund_code = code.replace(".OF", "")
                    nav = _fetch_fund_nav(fund_code)
                    if nav and nav > 0:
                        price = nav

                # A股ETF（.SZ/.SH后缀）及未获取到价格的场外基金：通过腾讯API获取
                if not price and (
                    code.endswith(".SZ") or code.endswith(".SH") or code.endswith(".OF")
                ):
                    quote_info = fetch_tencent_quote(code)
                    if quote_info:
                        price = quote_info.get("price")

                if not price or price <= 0:
                    continue

                # 更新持仓价格和金额
                h.price = round(price, 4)
                h.amount = round(h.quantity * price, 2)

                # 折算人民币金额
                rate = get_rate(db, h.currency, "CNY")
                if rate and rate > 0:
                    h.amount_cny = round(h.amount * rate, 2)
                else:
                    h.amount_cny = h.amount

                # 写入价格缓存
                _save_price_cache(db, code, today, price, quote_info)
                updated += 1

            except Exception as e:
                logger.warning("抓取行情失败 [%s]: %s", code, e)
                continue

        db.commit()
        logger.info("行情抓取完成 (user_id=%s)，更新 %d/%d 只持仓", user_id, updated, len(holdings))
        return {
            "holdings_total": len(holdings),
            "updated": updated,
            "preflight": preflight,
            "user_id": user_id,
        }

    except Exception as e:
        logger.error("行情抓取任务异常: %s", e, exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


def _save_price_cache(
    db: Session,
    stock_code: str,
    trade_date: date,
    close_px: float,
    quote_info: dict | None,
):
    """将行情数据写入 price_cache 表"""
    try:
        cache = PriceCache(
            stock_code=stock_code,
            trade_date=trade_date,
            open_px=quote_info.get("open") if quote_info else None,
            close_px=close_px,
            high_px=quote_info.get("high") if quote_info else None,
            low_px=quote_info.get("low") if quote_info else None,
            volume=quote_info.get("volume") if quote_info else None,
            source=quote_info.get("source", "scheduler") if quote_info else "scheduler",
        )
        db.add(cache)
    except Exception as e:
        logger.warning("写入价格缓存失败 [%s]: %s", stock_code, e)


# ---------- 任务2：财务基本面数据更新 ----------

@track_run("financial_fundamentals")
def job_update_financial_fundamentals():
    """每日7:00/19:00执行：增量抓取财务基本面数据并运行穿透计算"""
    db: Session = SessionLocal()
    try:
        from crawlers.price_data import get_stock_info, fetch_yfinance_info
        from services.penetration import PenetrationEngine

        today = date.today()

        # 获取所有美股持仓代码
        us_holdings = db.query(Holding).filter(
            Holding.asset_type.in_([
                AssetType.US_STOCK.value,
                AssetType.US_ETF.value,
            ])
        ).all()

        # 增量过滤：只抓取今日尚未缓存的股票
        cached_codes = set(
            row[0] for row in db.query(StockInfoCache.stock_code)
            .filter(StockInfoCache.updated_at >= today)
            .all()
        )

        updated = 0
        for h in us_holdings:
            code = h.security_code
            if code in cached_codes:
                continue

            try:
                # 先用腾讯API获取实时行情信息
                info = get_stock_info(code)
                # 再用yfinance补充财务数据
                yf_info = fetch_yfinance_info(code)

                # 合并两个数据源
                merged = {}
                if info:
                    merged.update(info)
                if yf_info:
                    # yfinance数据覆盖补充（保留已有字段）
                    for k, v in yf_info.items():
                        if k not in merged or merged[k] is None:
                            merged[k] = v

                if not merged:
                    continue

                # 写入 stock_info_cache
                existing = db.query(StockInfoCache).filter(
                    StockInfoCache.stock_code == code
                ).first()

                if existing:
                    existing.stock_name = merged.get("name") or existing.stock_name
                    existing.data_json = merged
                    existing.updated_at = datetime.utcnow()
                else:
                    db.add(StockInfoCache(
                        stock_code=code,
                        stock_name=merged.get("name", ""),
                        data_json=merged,
                        updated_at=datetime.utcnow(),
                    ))

                updated += 1

            except Exception as e:
                logger.warning("抓取基本面失败 [%s]: %s", code, e)
                continue

        db.commit()
        logger.info("基本面数据更新完成，新增/更新 %d 只股票", updated)

        # 基本面更新后运行穿透计算
        try:
            engine = PenetrationEngine(db)
            results = engine.calculate()
            logger.info("穿透计算完成，生成 %d 条结果", len(results))
        except Exception as e:
            logger.error("穿透计算异常: %s", e, exc_info=True)

    except Exception as e:
        logger.error("基本面数据更新任务异常: %s", e, exc_info=True)
        db.rollback()
    finally:
        db.close()


# ---------- 任务3：行业/爬虫数据更新 ----------

@track_run("industry_crawler_data")
def job_update_industry_crawler_data():
    """每日6:00/20:00执行：更新ETF映射、指数成分股、沪深300基准、汇率"""
    db: Session = SessionLocal()
    try:
        from crawlers.etf_index import crawl_fund_index_map
        from crawlers.index_constituents import crawl_constituents
        from crawlers.exchange_rates import update_rates_today
        from services.csi300 import Csi300Analyzer
        from config import CSI300_CODE

        # 1. 更新ETF→指数映射
        try:
            count = crawl_fund_index_map(db)
            logger.info("ETF映射更新完成，处理 %d 只基金", count)
        except Exception as e:
            logger.error("ETF映射更新失败: %s", e, exc_info=True)

        # 2. 更新沪深300成分股
        try:
            constituents = crawl_constituents(CSI300_CODE, db)
            logger.info("沪深300成分股更新完成，获取 %d 只成分股", len(constituents))
        except Exception as e:
            logger.error("沪深300成分股更新失败: %s", e, exc_info=True)

        # 3. 重新计算沪深300基准数据
        try:
            analyzer = Csi300Analyzer(db)
            result = analyzer.recalc_baselines()
            logger.info("沪深300基准重算完成: %s", result)
        except Exception as e:
            logger.error("沪深300基准重算失败: %s", e, exc_info=True)

        # 4. 更新汇率
        try:
            count = update_rates_today(db)
            logger.info("汇率更新完成，更新 %d 条记录", count)
        except Exception as e:
            logger.error("汇率更新失败: %s", e, exc_info=True)

    except Exception as e:
        logger.error("行业/爬虫数据更新任务异常: %s", e, exc_info=True)
    finally:
        db.close()


# ---------- 任务4：90 天历史价完整性检查 + 补缺 ----------

def _expected_trading_dates_legacy(days: int) -> list:
    """兜底：按 Mon-Fri 生成预期交易日（日历表为空时降级）"""
    from datetime import timedelta
    out = []
    today = date.today()
    for k in range(days + 1):
        d = today - timedelta(days=k)
        if d.weekday() < 5:
            out.append(d)
    return out


@track_run("backfill_gaps")
def job_backfill_gaps(days: int = 90):
    """检查所有 holding 过去 N 天的 price_cache 完整性，缺哪补哪。
    使用交易日历（按 holding 所属市场）判断应补哪些日期。"""
    db: Session = SessionLocal()
    try:
        from crawlers.price_data import fetch_price_history
        from services.trading_calendar import expected_trading_dates, _market_for_code

        holdings = db.query(Holding).all()
        results = []
        filled_total = 0

        for h in holdings:
            code = h.security_code
            try:
                # 该 holding 所属市场的预期交易日（按日历）
                mkt = _market_for_code(code)
                try:
                    expected = set(expected_trading_dates(mkt, days, db))
                except Exception:
                    expected = set(_expected_trading_dates_legacy(days))

                # 已有日期集合
                existing = set(
                    row[0] for row in
                    db.query(PriceCache.trade_date)
                    .filter(PriceCache.stock_code == code)
                    .all()
                )
                # 缺哪些日期
                missing = expected - existing
                if not missing:
                    results.append({"code": code, "status": "complete"})
                    continue

                # 拉历史价（用 days 大窗口确保覆盖）
                try:
                    history = fetch_price_history(code, days + 5)
                except Exception as e:
                    results.append({"code": code, "status": "fetch_error", "missing": len(missing), "error": str(e)[:100]})
                    continue

                # 只补缺失的日期
                history_by_date = {}
                for p in history:
                    try:
                        d = date.fromisoformat(p["date"])
                        history_by_date[d] = p
                    except (ValueError, TypeError):
                        continue

                written = 0
                for d in missing:
                    p = history_by_date.get(d)
                    if not p:
                        continue
                    db.add(PriceCache(
                        stock_code=code,
                        trade_date=d,
                        open_px=p.get("open"),
                        close_px=p.get("close"),
                        high_px=p.get("high"),
                        low_px=p.get("low"),
                        volume=p.get("volume"),
                        source="gap_fill",
                    ))
                    written += 1

                if written:
                    db.commit()
                filled_total += written
                results.append({"code": code, "status": "ok", "missing": len(missing), "filled": written, "market": mkt})
            except Exception as e:
                results.append({"code": code, "status": "error", "error": str(e)[:100]})
                continue

        from collections import Counter
        statuses = Counter(r["status"] for r in results)
        logger.info(
            "历史价完整性检查完成: %s, 共补 %d 条",
            dict(statuses), filled_total,
        )
        return {"status": "ok", "summary": dict(statuses), "filled_total": filled_total, "details": results}
    except Exception as e:
        import traceback
        logger.error("历史价补缺任务异常: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)[:500]}
    finally:
        db.close()


# ---------- 调度器启停 ----------

def _run_code_map_preflight(db: Session, pools: tuple[str, ...] = ("holdings", "drilled")) -> dict:
    """代码映射覆盖率 pre-flight（在 fetch 任务前调用，记录缺失而非阻塞）。

    返回 summary dict — caller 负责写入 _JOB_LAST_RUN 字段供前端展示。
    即使有 missing 也不抛异常：避免一条规则缺失导致整批抓取停摆。
    """
    try:
        from scripts.check_code_map_coverage import run as coverage_run
        reports, summary = coverage_run(list(pools))
        total_missing = summary.get("total_missing", 0)
        if total_missing > 0:
            examples = []
            for p in summary.get("pools", []):
                for ex in p.get("missing_examples", [])[:3]:
                    examples.append(f"{p['name']}/{ex['code']}/{ex['api']}")
            logger.warning(
                "pre-flight 代码映射缺失 %d 条（%s）— 拉取时这些 code 可能失败，请补 _default_transform",
                total_missing, ", ".join(examples[:6]),
            )
        else:
            logger.info("pre-flight 代码映射覆盖率 OK（%d 池全部 mapped）", len(pools))
        return summary
    except Exception as e:
        logger.warning("pre-flight 覆盖率检查失败（非阻塞）: %s", e)
        return {"error": str(e)[:300], "preflight_skipped": True}


@track_run("fill_snapshot_gaps_smart")
def job_fill_snapshot_gaps_smart(days: int = 15, force: bool = False):
    """Smart Gap-Fill：扫描 snapshot 表所有股票过去 N 个交易日的 PriceCache 缺口，
    用腾讯 K 线 / yfinance 多 API 兜底补全，**不覆盖**已有数据。
    最后回写 snapshot.current_price（仅更新到更新日期）。

    设计原则：
      - 不动 current_price 已存在的快照（除非 PriceCache 有更晚的日期）
      - 每个 gap 只查一次（PriceCache UNIQUE 约束保证幂等）
      - 跳过 .BJ（北交所，Tencent 不支持）
      - force=True 跳过 is_any_market_open_today 门控

    时机：每日 06:00 + 20:00（cron），覆盖美股昨日收盘 + A/H 股当日收盘。

    入口先跑代码映射 pre-flight：记录 missing 到 last_result，不阻塞执行。
    """
    from services.price_filler import fill_snapshot_gaps_smart

    db = SessionLocal()
    try:
        # Pre-flight：检查持仓 + 下钻池的代码映射完整性
        preflight = _run_code_map_preflight(db, pools=("holdings", "drilled"))

        if not force:
            try:
                from services.trading_calendar import is_any_market_open_today
                if not is_any_market_open_today(db):
                    logger.info(
                        "全市场休市（昨天是周末/节假日），跳过 snapshot 补缺；"
                        "force=True 可绕过"
                    )
                    return {"skipped": "all_markets_closed", "preflight": preflight}
            except Exception as e:
                logger.warning("日历门控失败，继续执行: %s", e)

        result = fill_snapshot_gaps_smart(db, days=days, max_codes=10000, sleep_between=0.0)
        logger.info(
            "snapshot gap-fill 完成: checked=%d gaps_found=%d filled=%d "
            "snapshots_updated=%d api=%s elapsed=%ss",
            result["stocks_checked"],
            result["gaps_found"],
            result["gaps_filled"],
            result["snapshots_updated"],
            result["api_breakdown"],
            result["elapsed_seconds"],
        )
        result["preflight"] = preflight
        return result
    except Exception as e:
        logger.error("snapshot gap-fill 异常: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def start_scheduler():
    """启动后台定时任务调度器"""
    global scheduler

    if scheduler and scheduler.running:
        logger.warning("调度器已在运行中，跳过重复启动")
        return

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    # 任务1：实时行情抓取 — 交易时段每15分钟
    scheduler.add_job(
        job_fetch_realtime_prices,
        "interval",
        minutes=15,
        id="realtime_prices",
        name="实时行情抓取",
        max_instances=1,
        misfire_grace_time=60,
    )

    # 任务1b：snapshot 智能补缺 — 每日 06:00 + 20:00
    # 覆盖美股昨日收盘 + A/H 股当日收盘；2 次/天避免限流
    scheduler.add_job(
        job_fill_snapshot_gaps_smart,
        "cron",
        hour="6,20",
        minute=0,
        id="fill_snapshot_gaps_smart",
        name="snapshot 智能补缺（15 天窗口）",
        max_instances=1,
        misfire_grace_time=600,
        kwargs={"days": 15},
    )

    # 任务2：财务基本面更新 — 每日 6:15 / 20:15（紧跟主批次 5min 后）
    scheduler.add_job(
        job_update_financial_fundamentals,
        "cron",
        hour="6,20",
        minute=15,
        id="financial_fundamentals",
        name="财务基本面更新",
        max_instances=1,
        misfire_grace_time=300,
    )

    # 任务3：行业/爬虫数据更新 — 每日 6:00 / 20:00（主批次）
    scheduler.add_job(
        job_update_industry_crawler_data,
        "cron",
        hour="6,20",
        minute=0,
        id="industry_crawler_data",
        name="行业/爬虫数据更新",
        max_instances=1,
        misfire_grace_time=300,
    )

    # 任务4：360 天历史价完整性检查 — 每日 6:05 / 20:05（跟着主批次做完整性检查）
    # Holding 是动态的（不只是固定 44 只），用 360 天窗口覆盖完整年度
    scheduler.add_job(
        job_backfill_gaps,
        "cron",
        hour="6,20",
        minute=5,
        id="backfill_gaps",
        name="历史价补缺",
        max_instances=1,
        misfire_grace_time=600,
        kwargs={"days": 360},
    )

    # 任务5：全球快讯 — 每日 6:20 / 20:20 (a-stock-data skill §5.3)
    scheduler.add_job(
        job_crawl_global_news,
        "cron",
        hour="6,20",
        minute=20,
        id="info_global_news",
        name="全球快讯抓取",
        max_instances=1,
        misfire_grace_time=600,
    )

    # 任务6：个股新闻 — 每日 6:25 / 20:25 (持仓成分股, skill §5.1)
    scheduler.add_job(
        job_crawl_stock_news,
        "cron",
        hour="6,20",
        minute=25,
        id="info_stock_news",
        name="个股新闻抓取",
        max_instances=1,
        misfire_grace_time=900,
    )

    # 任务7：公告 + 研报 — 每日 6:35 / 20:35（每天，不再 */3）(skill §2.1 + §7.1)
    scheduler.add_job(
        job_crawl_announcements_and_research,
        "cron",
        hour="6,20",
        minute=35,
        id="info_announcements_research",
        name="公告+研报抓取",
        max_instances=1,
        misfire_grace_time=1800,
    )

    # 任务8：同花顺热点 — 交易日 15:35 (skill §3.1, 用户确认保留盘中后抓取)
    scheduler.add_job(
        job_crawl_hot_stocks,
        "cron",
        hour=15,
        minute=35,
        id="info_hot_stocks",
        name="同花顺热点抓取",
        max_instances=1,
        misfire_grace_time=600,
    )

    # 任务8b：同花顺热点兜底 — 交易日 6:10 (dedup signal_date==today 防止重复)
    scheduler.add_job(
        job_crawl_hot_stocks,
        "cron",
        hour=6,
        minute=10,
        id="info_hot_stocks_fallback",
        name="同花顺热点兜底抓取",
        max_instances=1,
        misfire_grace_time=600,
    )

    # 任务9：数据补足检测（每日 6:50 — 早于 7:00 财务任务）
    scheduler.add_job(
        job_detect_data_gaps,
        "cron",
        hour=6,
        minute=50,
        id="detect_data_gaps",
        name="数据补足检测",
        max_instances=1,
        misfire_grace_time=300,
    )

    # 任务10：公共下钻截面生成（每日 18:00 — A股/港股收盘后，生成 fund_drill_snapshot）
    scheduler.add_job(
        job_generate_drill_snapshot,
        "cron",
        hour=18,
        minute=0,
        id="drill_snapshot",
        name="公共下钻截面生成",
        max_instances=1,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info(
        "调度器已启动，注册 %d 个定时任务",
        len(scheduler.get_jobs()),
    )


@track_run("detect_data_gaps")
def job_detect_data_gaps():
    """每日 6:50 — 扫描 3 类数据缺口写入 data_gap_report"""
    from services.data_gap_detector import detect_all_gaps
    db = SessionLocal()
    try:
        return detect_all_gaps(db)
    except Exception as e:
        logger.error("detect_data_gaps failed: %s", e, exc_info=True)
        raise
    finally:
        db.close()


@track_run("drill_snapshot")
def job_generate_drill_snapshot(as_of_date=None):
    """T+1 收盘后生成公共下钻截面（fund_drill_snapshot — 2026-06-24 引入）。

    算法（参考 services/drill_snapshot.py）：
      对每只可下钻基金 × as_of_date：
        读 index_constituents[最近月份] + PriceCache[T]
        校验 95% 价格可得，缺失用 T-1 价
        算 shares_equivalent = fund_price × 0.95 × (weight/100) / current_price
    默认生成最近一个交易日（市场全部收盘后）。
    """
    from datetime import date as _date
    from services.drill_snapshot import generate_drill_snapshot_for_date
    if as_of_date is None:
        # 取最近一个有 PriceCache 的交易日（视为最近收盘日）
        from models import PriceCache
        from sqlalchemy import func
        last = c = None  # noqa
        db_probe = SessionLocal()
        try:
            c = db_probe.query(func.max(PriceCache.trade_date)).scalar()
        finally:
            db_probe.close()
        as_of_date = c
    if as_of_date is None:
        logger.warning("drill_snapshot: no PriceCache date found, skip")
        return {"skipped": "no_price_cache_date"}
    db = SessionLocal()
    try:
        return generate_drill_snapshot_for_date(db, as_of_date)
    except Exception as e:
        logger.error("drill_snapshot failed: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def stop_scheduler():
    """停止后台定时任务调度器"""
    global scheduler

    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("调度器已停止")
    else:
        logger.info("调度器未在运行，无需停止")

    scheduler = None


# ============================================================================
# 资讯数据抓取任务 (a-stock-data skill §3.1 / §5 / §7)
# ============================================================================


def _info_target_codes(db) -> list[str]:
    """资讯拉取的目标股票集合: 穿透后的成分股 + 自选股 (去重).
    美股 / 港股不拉东财资讯, 避免无效请求.
    """
    from models import PenetrationSnapshot
    rows = db.query(PenetrationSnapshot.stock_code).distinct().all()
    codes = {r[0] for r in rows if r[0] and not r[0].startswith(("us", "US")) and not r[0].endswith((".OF", ".HK"))}
    return sorted(codes)


@track_run("info_global_news")
def job_crawl_global_news():
    """每日 07:30 / 19:30: 拉全球快讯 (skill §5.3)."""
    db: Session = SessionLocal()
    try:
        from crawlers.news_eastmoney import fetch_global_flash_news
        from services.info_service import upsert_global_flash_news
        rows = fetch_global_flash_news(page_size=100)
        written = upsert_global_flash_news(db, rows)
        logger.info("全球快讯抓取: fetched=%d written=%d", len(rows), written)
    except Exception as e:
        logger.error("全球快讯抓取异常: %s", e, exc_info=True)
    finally:
        db.close()


@track_run("info_stock_news")
def job_crawl_stock_news():
    """每日 18:00: 拉穿透成分股的个股新闻 (skill §5.1)."""
    db: Session = SessionLocal()
    try:
        from crawlers.news_eastmoney import fetch_stock_news
        from services.info_service import upsert_stock_news
        codes = _info_target_codes(db)
        total_written = 0
        for code in codes[:50]:  # 限流, 单次最多 50 只
            try:
                rows = fetch_stock_news(code, page_size=10)
                written = upsert_stock_news(db, code, rows)
                total_written += written
            except Exception as e:
                logger.warning("个股新闻抓取失败 code=%s: %s", code, e)
                continue
        logger.info("个股新闻抓取: codes=%d written=%d", min(len(codes), 50), total_written)
    except Exception as e:
        logger.error("个股新闻抓取异常: %s", e, exc_info=True)
    finally:
        db.close()


@track_run("info_announcements_research")
def job_crawl_announcements_and_research():
    """每 3 日 21:00: 拉穿透成分股的公告 + 研报."""
    db: Session = SessionLocal()
    try:
        from crawlers.announcement_cninfo import fetch_announcements
        from crawlers.research_em import fetch_reports
        from services.info_service import upsert_announcements, upsert_research_reports
        codes = _info_target_codes(db)
        ann_total = res_total = 0
        for code in codes[:30]:  # 限流: 公告/研报较慢, 单次 30 只
            try:
                ann_rows = fetch_announcements(code, page_size=20)
                ann_total += upsert_announcements(db, code, ann_rows)
            except Exception as e:
                logger.warning("公告抓取失败 code=%s: %s", code, e)
            try:
                res_rows = fetch_reports(code, max_pages=1)
                res_total += upsert_research_reports(db, code, res_rows)
            except Exception as e:
                logger.warning("研报抓取失败 code=%s: %s", code, e)
        logger.info("公告+研报抓取: codes=%d ann_written=%d res_written=%d",
                    min(len(codes), 30), ann_total, res_total)
    except Exception as e:
        logger.error("公告+研报抓取异常: %s", e, exc_info=True)
    finally:
        db.close()


@track_run("info_hot_stocks")
def job_crawl_hot_stocks():
    """交易日 15:35: 拉同花顺当日热点 + 题材归因 (skill §3.1)."""
    from datetime import date as _date
    db: Session = SessionLocal()
    try:
        from crawlers.signal_ths import fetch_hot_stocks
        from services.info_service import upsert_hot_stocks
        rows = fetch_hot_stocks(_date.today())
        written = upsert_hot_stocks(db, _date.today(), rows)
        logger.info("同花顺热点抓取: fetched=%d written=%d", len(rows), written)
    except Exception as e:
        logger.error("同花顺热点抓取异常: %s", e, exc_info=True)
    finally:
        db.close()


# ============================================================================
# Task 7: JOB_DISPATCH + trigger_job — 手动触发 + 任务历史记录
# ============================================================================

# job_id → {name, func}：供 trigger_job 查找。覆盖 start_scheduler 注册的全部 job。
JOB_DISPATCH: dict[str, dict] = {
    "realtime_prices": {"name": "实时行情抓取", "func": job_fetch_realtime_prices},
    "fill_snapshot_gaps_smart": {"name": "snapshot 智能补缺", "func": job_fill_snapshot_gaps_smart},
    "industry_crawler_data": {"name": "行业/爬虫数据更新", "func": job_update_industry_crawler_data},
    "financial_fundamentals": {"name": "财务基本面更新", "func": job_update_financial_fundamentals},
    "backfill_gaps": {"name": "历史价补缺", "func": job_backfill_gaps},
    "info_global_news": {"name": "全球快讯抓取", "func": job_crawl_global_news},
    "info_stock_news": {"name": "个股新闻抓取", "func": job_crawl_stock_news},
    "info_announcements_research": {"name": "公告+研报抓取", "func": job_crawl_announcements_and_research},
    "info_hot_stocks": {"name": "同花顺热点抓取", "func": job_crawl_hot_stocks},
    "detect_data_gaps": {"name": "数据补足检测", "func": job_detect_data_gaps},
    "drill_snapshot": {"name": "公共下钻截面生成", "func": job_generate_drill_snapshot},
}


def trigger_job(db: Session, job_id: str, triggered_by: str = "manual", **kwargs) -> dict:
    """手动触发一个 job（带 DataPullTask 任务追踪）。

    由 API 端点 /api/admin/data-pull-tasks/trigger/{job_id} 调用。
    使用传入的 db 会话记录 record_task_start/finish；同时通过 contextvar
    通知 track_run 跳过自动记录（避免重复）。

    Args:
        db: 数据库会话（用于 record_task_start/finish）
        job_id: JOB_DISPATCH 中的 job 标识
        triggered_by: 触发者标识，如 "manual" / "manual:123"
        **kwargs: 传递给 job 函数的参数（如 force=True, days=30）

    Returns:
        成功: {"status": "ok", "job_id": ..., "records": N, "result": ...}
        失败: {"status": "error", "job_id": ..., "message": ...}
    """
    if job_id not in JOB_DISPATCH:
        return {
            "status": "error",
            "message": f"未知 job_id: {job_id}. 可用: {sorted(JOB_DISPATCH.keys())}",
        }

    entry = JOB_DISPATCH[job_id]
    func = entry["func"]
    job_name = entry["name"]

    # 设置 triggered_by 上下文 → track_run 据此跳过自动记录（由本函数负责记录）
    token = _triggered_by_ctx.set(triggered_by)
    try:
        # 记录任务开始（使用调用方传入的 db）
        task = record_task_start(db, job_id, job_name, triggered_by)
        task_id = task.get("id")
    except Exception as e:
        logger.warning("trigger_job: record_task_start 失败 (job=%s): %s", job_id, e)
        task_id = None

    try:
        result = func(**kwargs) if kwargs else func()
        records = _extract_record_count(result)
        if task_id is not None:
            try:
                record_task_finish(db, task_id, "SUCCESS", records_pulled=records)
            except Exception as e:
                logger.warning("trigger_job: record_task_finish 失败 (task_id=%s): %s", task_id, e)
        return {
            "status": "ok",
            "job_id": job_id,
            "records": records,
            "result": result if isinstance(result, dict)
                      else {"value": str(result)[:200] if result is not None else None},
        }
    except Exception as e:
        logger.error("手动触发 job 失败 (job=%s): %s", job_id, e, exc_info=True)
        if task_id is not None:
            try:
                record_task_finish(db, task_id, "FAILED", error_message=str(e)[:500])
            except Exception as rec_err:
                logger.warning("trigger_job: record_task_finish 失败 (task_id=%s): %s", task_id, rec_err)
        return {"status": "error", "job_id": job_id, "message": str(e)[:300]}
    finally:
        _triggered_by_ctx.reset(token)
