"""
TDD tests for crawlers/_http.py rate limiting + retry behavior.

Phase 1 of the data-pulling refactor: ensure tencent_get / ths_get / em_post
enforce minimum call intervals and retry on transient failures, mirroring the
pattern already established for em_get.

Each test resets the module-level _last_call timestamps so prior calls don't
poison the timing window.
"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import httpx
import pytest

from crawlers import _http
from crawlers._http import tencent_get, ths_get
import config

# New constants expected to land in this phase. Pull defensively so each test
# fails with a clear assertion rather than a collection ImportError.
TENCENT_MIN_INTERVAL = getattr(config, "TENCENT_MIN_INTERVAL", None)
THS_MIN_INTERVAL = getattr(config, "THS_MIN_INTERVAL", None)

# em_post is expected to be added in this phase. Import defensively so that
# each test reports a clear assertion failure rather than collection error.
try:
    from crawlers._http import em_post  # type: ignore
except ImportError:
    em_post = None  # type: ignore[assignment]


# -----------------------------------------------------------------------------
# Fixtures: reset module-level rate-limit state before each test
# -----------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_throttle_state():
    """Zero out all _last_call timestamps so tests are independent."""
    _http._em_last_call = 0.0
    _http._tencent_last_call = 0.0
    _http._ths_last_call = 0.0
    yield
    _http._em_last_call = 0.0
    _http._tencent_last_call = 0.0
    _http._ths_last_call = 0.0


def _fake_response(status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    return resp


# -----------------------------------------------------------------------------
# Rate-limit tests
# -----------------------------------------------------------------------------

def test_tencent_get_enforces_min_interval():
    """Two serial tencent_get calls must sleep at least TENCENT_MIN_INTERVAL
    between the first request firing and the second request firing."""
    assert TENCENT_MIN_INTERVAL is not None, (
        "config.TENCENT_MIN_INTERVAL must be defined"
    )
    sleeps: list[float] = []

    with patch.object(_http, "time") as mock_time, \
         patch.object(_http, "random") as mock_random, \
         patch.object(_http, "_get_tencent_client") as mock_client_factory:
        # Freeze time so the first call doesn't trigger any wait (last_call=0)
        mock_time.time.return_value = 1000.0
        mock_random.uniform.return_value = 0.0  # disable jitter
        mock_time.sleep.side_effect = lambda s: sleeps.append(s)
        mock_client_factory.return_value.get.return_value = _fake_response(200)

        tencent_get("http://x")
        tencent_get("http://x")

    total_wait = sum(sleeps)
    # First call: wait = 1.0 - 0 = 1.0 (still triggers since last_call=0 < 1.0)
    # Actually with last_call=0 and time.time()=1000, wait = TENCENT_MIN_INTERVAL - 1000
    # which is negative, so first call sleeps 0.
    # Second call: last_call=1000, wait = TENCENT_MIN_INTERVAL - 0 = TENCENT_MIN_INTERVAL
    # So total wait >= TENCENT_MIN_INTERVAL is the assertion.
    assert total_wait >= TENCENT_MIN_INTERVAL - 0.01, (
        f"Expected total wait >= {TENCENT_MIN_INTERVAL}s, got {total_wait}s "
        f"(sleeps={sleeps})"
    )


def test_ths_get_enforces_min_interval():
    """Same as above for ths_get."""
    assert THS_MIN_INTERVAL is not None, "config.THS_MIN_INTERVAL must be defined"
    sleeps: list[float] = []

    with patch.object(_http, "time") as mock_time, \
         patch.object(_http, "random") as mock_random, \
         patch.object(_http, "_get_ths_client") as mock_client_factory:
        mock_time.time.return_value = 1000.0
        mock_random.uniform.return_value = 0.0
        mock_time.sleep.side_effect = lambda s: sleeps.append(s)
        mock_client_factory.return_value.get.return_value = _fake_response(200)

        ths_get("http://x")
        ths_get("http://x")

    total_wait = sum(sleeps)
    assert total_wait >= THS_MIN_INTERVAL - 0.01, (
        f"Expected total wait >= {THS_MIN_INTERVAL}s, got {total_wait}s "
        f"(sleeps={sleeps})"
    )


# -----------------------------------------------------------------------------
# Retry tests
# -----------------------------------------------------------------------------

def test_tencent_get_retries_on_403_then_succeeds():
    """403 on first attempt should trigger a retry; second attempt 200 returns."""
    sleep_durations: list[float] = []
    call_count = {"n": 0}

    def fake_get(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_response(403)
        return _fake_response(200)

    with patch.object(_http, "time") as mock_time, \
         patch.object(_http, "random") as mock_random, \
         patch.object(_http, "_get_tencent_client") as mock_client_factory:
        mock_time.time.return_value = 1000.0
        mock_random.uniform.return_value = 0.0
        mock_time.sleep.side_effect = lambda s: sleep_durations.append(s)
        mock_client_factory.return_value.get.side_effect = fake_get

        resp = tencent_get("http://x")

    assert resp is not None, "tencent_get should return the successful retry response"
    assert resp.status_code == 200
    assert call_count["n"] == 2, f"Expected 2 calls (1 fail + 1 success), got {call_count['n']}"
    # At least one backoff sleep (the 2+random.uniform retry sleep on 403)
    assert any(s >= 1.5 for s in sleep_durations), (
        f"Expected a backoff sleep ~2s on 403, got {sleep_durations}"
    )


def test_ths_get_retries_on_403_then_succeeds():
    sleep_durations: list[float] = []
    call_count = {"n": 0}

    def fake_get(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_response(403)
        return _fake_response(200)

    with patch.object(_http, "time") as mock_time, \
         patch.object(_http, "random") as mock_random, \
         patch.object(_http, "_get_ths_client") as mock_client_factory:
        mock_time.time.return_value = 1000.0
        mock_random.uniform.return_value = 0.0
        mock_time.sleep.side_effect = lambda s: sleep_durations.append(s)
        mock_client_factory.return_value.get.side_effect = fake_get

        resp = ths_get("http://x")

    assert resp is not None
    assert resp.status_code == 200
    assert call_count["n"] == 2


def test_tencent_get_returns_none_on_connect_error():
    """Connection error should be swallowed and return None (not raise)."""
    with patch.object(_http, "time") as mock_time, \
         patch.object(_http, "random") as mock_random, \
         patch.object(_http, "_get_tencent_client") as mock_client_factory:
        mock_time.time.return_value = 1000.0
        mock_random.uniform.return_value = 0.0
        mock_time.sleep.return_value = None
        mock_client_factory.return_value.get.side_effect = httpx.ConnectError("refused")

        # Should NOT raise; must return None
        result = tencent_get("http://x")

    assert result is None, "tencent_get must swallow connect errors and return None"


def test_ths_get_returns_none_on_connect_error():
    with patch.object(_http, "time") as mock_time, \
         patch.object(_http, "random") as mock_random, \
         patch.object(_http, "_get_ths_client") as mock_client_factory:
        mock_time.time.return_value = 1000.0
        mock_random.uniform.return_value = 0.0
        mock_time.sleep.return_value = None
        mock_client_factory.return_value.get.side_effect = httpx.ReadTimeout("slow")

        result = ths_get("http://x")

    assert result is None


def test_tencent_get_gives_up_after_max_retries_on_403():
    """After max_retries consecutive 403s, return the last 403 response (not None)."""
    with patch.object(_http, "time") as mock_time, \
         patch.object(_http, "random") as mock_random, \
         patch.object(_http, "_get_tencent_client") as mock_client_factory:
        mock_time.time.return_value = 1000.0
        mock_random.uniform.return_value = 0.0
        mock_time.sleep.return_value = None
        mock_client_factory.return_value.get.return_value = _fake_response(403)

        resp = tencent_get("http://x", max_retries=2)

    # Last attempt's 403 response should be returned (matches em_get behavior)
    assert resp is not None
    assert resp.status_code == 403


# -----------------------------------------------------------------------------
# em_post helper tests
# -----------------------------------------------------------------------------

def test_em_post_returns_response_on_success():
    """em_post helper should mirror em_get's rate-limit+retry on POST."""
    assert em_post is not None, "em_post helper must be defined in crawlers/_http.py"
    with patch.object(_http, "time") as mock_time, \
         patch.object(_http, "random") as mock_random, \
         patch.object(_http, "_get_em_client") as mock_client_factory:
        mock_time.time.return_value = 1000.0
        mock_random.uniform.return_value = 0.0
        mock_time.sleep.return_value = None
        mock_client_factory.return_value.post.return_value = _fake_response(200)

        resp = em_post("http://x", data={"a": 1})

    assert resp is not None
    assert resp.status_code == 200
    mock_client_factory.return_value.post.assert_called_once()


def test_em_post_returns_none_on_connect_error():
    assert em_post is not None, "em_post helper must be defined in crawlers/_http.py"
    with patch.object(_http, "time") as mock_time, \
         patch.object(_http, "random") as mock_random, \
         patch.object(_http, "_get_em_client") as mock_client_factory:
        mock_time.time.return_value = 1000.0
        mock_random.uniform.return_value = 0.0
        mock_time.sleep.return_value = None
        mock_client_factory.return_value.post.side_effect = httpx.ConnectError("refused")

        result = em_post("http://x", data={"a": 1})

    assert result is None