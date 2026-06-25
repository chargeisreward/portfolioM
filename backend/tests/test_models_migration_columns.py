"""验证 holdings/watchlist/access_sessions 加了 user_id"""
from database import Base
import models


def test_holding_has_user_id():
    cols = {c.name for c in Base.metadata.tables["holdings"].columns}
    assert "user_id" in cols


def test_watchlist_pk_is_composite():
    pks = [c.name for c in Base.metadata.tables["watchlist"].primary_key.columns]
    assert set(pks) == {"user_id", "code"}


def test_access_session_has_user_id():
    cols = {c.name for c in Base.metadata.tables["access_sessions"].columns}
    assert "user_id" in cols