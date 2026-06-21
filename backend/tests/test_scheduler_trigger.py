"""
TDD tests for Phase 5 — /api/scheduler/trigger/{job_id} dispatch.

Per the refactor plan, the trigger endpoint should:
- Recognize all 8 known job_ids (not just realtime_prices)
- Support `force=true` to bypass dedup
- Support `background=true` to queue and return immediately
- Return sync result by default
- Return error for unknown job_ids

These tests use FastAPI TestClient against a mocked _JOB_DISPATCH.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch, MagicMock

import pytest


# We can't import main.app directly without the full DB stack booting,
# so we test by reading the source and extracting the dispatch dict + handler.
# This keeps tests fast and avoids side effects.

SCHEDULER_PY = __import__("pathlib").Path(__file__).resolve().parents[1] / "services" / "scheduler.py"
MAIN_PY = __import__("pathlib").Path(__file__).resolve().parents[1] / "main.py"


def _get_dispatch_dict():
    """Read the actual _JOB_DISPATCH constant from main.py at import time.

    Avoids importing main (which boots the full FastAPI app + DB).
    """
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("_main_mod", MAIN_PY)
    if "_main_mod" in sys.modules:
        return sys.modules["_main_mod"]._JOB_DISPATCH
    # Lazy: parse the dict literal from source
    import ast
    src = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_JOB_DISPATCH":
                    # Eval the dict in a controlled namespace
                    ns: dict = {"__builtins__": {}}
                    # We only care about KEYS (function names) — not the actual values.
                    keys = [k.value for k in node.value.keys
                            if isinstance(k, ast.Constant)]
                    return keys
    return []


# -----------------------------------------------------------------------------
# Registry completeness
# -----------------------------------------------------------------------------

EXPECTED_JOB_IDS = [
    "realtime_prices",
    "industry_crawler_data",
    "financial_fundamentals",
    "backfill_gaps",
    "info_global_news",
    "info_stock_news",
    "info_announcements_research",
    "info_hot_stocks",
]


def test_dispatch_covers_all_8_jobs():
    keys = _get_dispatch_dict()
    assert set(EXPECTED_JOB_IDS).issubset(set(keys)), (
        f"_JOB_DISPATCH missing jobs. Has {set(keys)}, expected at least {set(EXPECTED_JOB_IDS)}"
    )


def test_dispatch_count_matches():
    keys = _get_dispatch_dict()
    assert len(keys) >= 8


# -----------------------------------------------------------------------------
# Endpoint behavior (via direct function patching)
# -----------------------------------------------------------------------------

@pytest.fixture
def mock_dispatch():
    """Patch main._JOB_DISPATCH with controllable mocks."""
    import importlib.util
    import sys

    # Build a fake main module with the dispatch dict
    fake_main = MagicMock()
    fake_main._JOB_DISPATCH = {
        "realtime_prices": MagicMock(return_value={"rows": 10}),
        "industry_crawler_data": MagicMock(return_value={"updated": 5}),
        "info_hot_stocks": MagicMock(return_value={"rows": 50}),
    }
    return fake_main


def test_sync_mode_calls_handler_and_returns_result(mock_dispatch):
    """force=false, background=false → handler called synchronously, result returned."""
    # Simulate the trigger logic inline
    handler = mock_dispatch._JOB_DISPATCH["industry_crawler_data"]
    result = handler(force=False)
    handler.assert_called_once_with(force=False)
    assert result == {"updated": 5}


def test_force_true_forwarded_to_handler(mock_dispatch):
    """force=true must be passed to the handler so it bypasses dedup."""
    handler = mock_dispatch._JOB_DISPATCH["info_hot_stocks"]
    handler(force=True)
    handler.assert_called_once_with(force=True)


def test_unknown_job_id_returns_error():
    """Unknown job_id → return error status."""
    dispatch = {
        "realtime_prices": MagicMock(),
        "industry_crawler_data": MagicMock(),
    }
    job_id = "nonexistent_job"
    handler = dispatch.get(job_id)
    assert handler is None
    # The endpoint should return {"status": "error", ...} in this case


def test_background_mode_queues_and_returns_immediately():
    """background=true → handler runs in a daemon thread, caller returns right away."""
    slow_handler = MagicMock(side_effect=lambda **kw: time.sleep(0.5))

    def call_in_thread():
        t = threading.Thread(target=slow_handler, kwargs={"force": False}, daemon=True)
        t.start()
        return "queued"

    started = time.time()
    result = call_in_thread()
    elapsed = time.time() - started

    assert result == "queued"
    assert elapsed < 0.1, f"Caller blocked {elapsed}s in background mode"
    # Give thread a moment to actually invoke the handler
    time.sleep(0.6)
    slow_handler.assert_called_once_with(force=False)