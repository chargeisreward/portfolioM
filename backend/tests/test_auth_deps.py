"""测试 middleware/auth.py 的依赖函数"""
import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DATABASE_URL"] = "sqlite:///:memory:"


@pytest.fixture(scope="module")
def db_session():
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    import models
    from database import Base
    Base.metadata.create_all(bind=engine)
    S = sessionmaker(bind=engine)
    return S()


_counter = [0]

def make_user(s, **kwargs):
    from models import User
    _counter[0] += 1
    # 始终使用唯一 username，避免 module-scope session 的 UNIQUE 冲突
    kwargs["username"] = f"u_{_counter[0]}"
    defaults = dict(password_hash="x", is_active=True)
    defaults.update(kwargs)
    u = User(**defaults)
    s.add(u); s.commit(); s.refresh(u)
    return u


class FakeRequest:
    def __init__(self, user=None):
        self.state = type("S", (), {})()
        if user:
            self.state.user = user


def test_require_user_no_user_raises():
    from middleware.auth import require_user
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        require_user(FakeRequest())
    assert e.value.status_code == 401


def test_require_user_returns_user(db_session):
    u = make_user(db_session, username="u1")
    from middleware.auth import require_user
    got = require_user(FakeRequest(user=u))
    assert got.id == u.id


def test_require_advisor_admin_ok(db_session):
    u = make_user(db_session, username="admin", is_admin=True)
    from middleware.auth import require_advisor
    got = require_advisor(FakeRequest(user=u))
    assert got.id == u.id


def test_require_advisor_user_403(db_session):
    u = make_user(db_session, username="plain", is_admin=False, is_advisor=False)
    from middleware.auth import require_advisor
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        require_advisor(FakeRequest(user=u))
    assert e.value.status_code == 403


def test_require_admin_user_403(db_session):
    u = make_user(db_session, username="plain", is_admin=False)
    from middleware.auth import require_admin
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        require_admin(FakeRequest(user=u))
    assert e.value.status_code == 403


def test_get_effective_user_id_self(db_session):
    u = make_user(db_session, username="self")
    from middleware.auth import get_effective_user_id
    assert get_effective_user_id(FakeRequest(), None, u, db_session) == u.id
    assert get_effective_user_id(FakeRequest(), u.id, u, db_session) == u.id


def test_get_effective_user_id_user_cannot(db_session):
    u = make_user(db_session, username="plain", is_admin=False, is_advisor=False)
    from middleware.auth import get_effective_user_id
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        get_effective_user_id(FakeRequest(), 999, u, db_session)
    assert e.value.status_code == 403


def test_get_effective_user_id_advisor_no_relation_403(db_session):
    advisor = make_user(db_session, username="adv", is_advisor=True)
    target = make_user(db_session, username="tgt")
    from middleware.auth import get_effective_user_id
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        get_effective_user_id(FakeRequest(), target.id, advisor, db_session)
    assert e.value.status_code == 403


def test_get_effective_user_id_advisor_with_relation_ok(db_session):
    from models import UserRelation
    advisor = make_user(db_session, username="adv2", is_advisor=True)
    target = make_user(db_session, username="tgt2")
    rel = UserRelation(advisor_user_id=advisor.id, client_user_id=target.id,
                       status="ACTIVE", initiator_user_id=target.id)
    db_session.add(rel); db_session.commit()
    from middleware.auth import get_effective_user_id
    assert get_effective_user_id(FakeRequest(), target.id, advisor, db_session) == target.id


def test_get_effective_user_id_admin_anyone(db_session):
    admin = make_user(db_session, username="admin2", is_admin=True)
    target = make_user(db_session, username="tgt3")
    from middleware.auth import get_effective_user_id
    assert get_effective_user_id(FakeRequest(), target.id, admin, db_session) == target.id