"""
TDD tests for Phase 4 — scheduler cron schedule consolidation.

Per the data-pulling refactor plan:
- Non-realtime jobs consolidated to fire at 6:00 and 20:00 daily
- Each job offset by 5 minutes to avoid contention
- realtime_prices stays interval=15min
- info_hot_stocks stays at 15:35 (trading-day snapshot) plus a 6:10 fallback
- info_announcements_research: daily (was every-3-day)

These tests parse `services/scheduler.py` source code to extract the
cron config of each registered job, then assert against the expected
schedule. Avoids booting the full scheduler (no HTTP / DB side effects).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SCHEDULER_PY = Path(__file__).resolve().parents[1] / "services" / "scheduler.py"


def _extract_jobs(source: str) -> dict[str, dict]:
    """Parse scheduler.add_job(...) calls from the source.

    Returns: {job_id: {'hour': str|int, 'minute': str|int, 'kind': str}}
    where kind is 'cron' or 'interval'.
    """
    tree = ast.parse(source)

    jobs: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # match scheduler.add_job(...)
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_job"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "scheduler"):
            continue

        # Parse kwargs (id, hour, minute, kind is implicit from 'cron' or 'interval' string)
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        job_id = kwargs.get("id")
        if job_id is None or not isinstance(job_id, ast.Constant):
            continue
        job_id = job_id.value

        # Detect kind from the 2nd positional arg ("cron" or "interval")
        kind = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            kind = node.args[1].value

        def _val(kwarg_value):
            return kwarg_value.value if isinstance(kwarg_value, ast.Constant) else getattr(kwarg_value, "value", kwarg_value)

        entry: dict = {"kind": kind}
        if "hour" in kwargs:
            entry["hour"] = _val(kwargs["hour"])
        if "minute" in kwargs:
            entry["minute"] = _val(kwargs["minute"])
        # APScheduler's interval trigger uses `minutes=` (plural)
        if "minutes" in kwargs:
            entry["minute"] = _val(kwargs["minutes"])
        jobs[job_id] = entry

    return jobs


@pytest.fixture(scope="module")
def jobs() -> dict[str, dict]:
    return _extract_jobs(SCHEDULER_PY.read_text(encoding="utf-8"))


# -----------------------------------------------------------------------------
# Real schedule tests
# -----------------------------------------------------------------------------

def test_realtime_prices_keeps_15_min_interval(jobs):
    assert jobs["realtime_prices"]["kind"] == "interval"
    assert jobs["realtime_prices"]["minute"] == 15


def test_industry_crawler_data_fires_at_6_and_20(jobs):
    assert jobs["industry_crawler_data"]["kind"] == "cron"
    assert str(jobs["industry_crawler_data"]["hour"]) == "6,20"
    assert jobs["industry_crawler_data"]["minute"] == 0


def test_financial_fundamentals_offset_15_minutes(jobs):
    assert jobs["financial_fundamentals"]["kind"] == "cron"
    assert str(jobs["financial_fundamentals"]["hour"]) == "6,20"
    assert jobs["financial_fundamentals"]["minute"] == 15


def test_backfill_gaps_offset_5_minutes(jobs):
    assert jobs["backfill_gaps"]["kind"] == "cron"
    assert str(jobs["backfill_gaps"]["hour"]) == "6,20"
    assert jobs["backfill_gaps"]["minute"] == 5


def test_global_news_offset_20_minutes(jobs):
    assert jobs["info_global_news"]["kind"] == "cron"
    assert str(jobs["info_global_news"]["hour"]) == "6,20"
    assert jobs["info_global_news"]["minute"] == 20


def test_stock_news_offset_25_minutes(jobs):
    assert jobs["info_stock_news"]["kind"] == "cron"
    assert str(jobs["info_stock_news"]["hour"]) == "6,20"
    assert jobs["info_stock_news"]["minute"] == 25


def test_announcements_research_offset_35_minutes_daily(jobs):
    """Was `*/3 21:00`, now daily at 6/20 min 35 (no more `day` arg)."""
    assert jobs["info_announcements_research"]["kind"] == "cron"
    assert str(jobs["info_announcements_research"]["hour"]) == "6,20"
    assert jobs["info_announcements_research"]["minute"] == 35


def test_hot_stocks_keeps_15_35_special(jobs):
    """info_hot_stocks at 15:35 is a special intraday snapshot — keep as-is."""
    assert jobs["info_hot_stocks"]["kind"] == "cron"
    assert jobs["info_hot_stocks"]["hour"] == 15
    assert jobs["info_hot_stocks"]["minute"] == 35


def test_hot_stocks_fallback_registered_at_6_10(jobs):
    """A second hot-stocks job at 6:10 retries yesterday if 15:35 missed."""
    assert "info_hot_stocks_fallback" in jobs, (
        "Missing 6:10 fallback for hot_stocks — dedup signal_date==today means "
        "it won't double-fetch on successful 15:35 runs"
    )
    assert jobs["info_hot_stocks_fallback"]["kind"] == "cron"
    assert jobs["info_hot_stocks_fallback"]["hour"] == 6
    assert jobs["info_hot_stocks_fallback"]["minute"] == 10


def test_no_old_single_fire_cron_remains(jobs):
    """Old 5:00 / 7:00 / 7:30 / 18:00 / 21:00 crons must be gone (replaced by 6/20)."""
    forbidden_single_hours = {5, 7, 18, 21}
    for jid, entry in jobs.items():
        if entry.get("kind") != "cron":
            continue
        if jid in ("info_hot_stocks", "info_hot_stocks_fallback"):
            continue
        h = entry.get("hour")
        # If hour is a single int (not the '6,20' string), it must not be one of the old hours
        if isinstance(h, int) and h in forbidden_single_hours:
            pytest.fail(f"{jid} still fires at old hour={h}")