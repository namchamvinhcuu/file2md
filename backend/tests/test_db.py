"""Test backend/db.py — mock asyncpg hoàn toàn, KHÔNG kết nối Postgres thật (máy dev chưa có
DATABASE_URL/server thật, xem CLAUDE.md task brief). Verify get_password_hash/create_user
gọi đúng SQL + params qua fake pool/connection.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

import db


class _FakeAcquireCM:
    """Giả lập `async with pool.acquire() as conn:` của asyncpg."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquireCM(self._conn)


@pytest.fixture
def fake_conn():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    return conn


@pytest.fixture(autouse=True)
def _reset_pool(monkeypatch):
    """Đảm bảo mỗi test tự set db._pool riêng (test isolation) — không rò global giữa test."""
    monkeypatch.setattr(db, "_pool", None)
    yield
    monkeypatch.setattr(db, "_pool", None)


async def test_get_password_hash_queries_correct_sql_and_username_param(monkeypatch, fake_conn):
    fake_conn.fetchrow = AsyncMock(return_value={"password_hash": "hashed123"})  # secret-allow: fake test hash, không phải secret thật
    monkeypatch.setattr(db, "_pool", _FakePool(fake_conn))

    result = await db.get_password_hash("alice")

    assert result == "hashed123"
    fake_conn.fetchrow.assert_awaited_once()
    query, *params = fake_conn.fetchrow.call_args.args
    assert "SELECT password_hash FROM file2md_users WHERE username" in query
    assert params == ["alice"]


async def test_get_password_hash_returns_none_when_user_not_found(monkeypatch, fake_conn):
    fake_conn.fetchrow = AsyncMock(return_value=None)
    monkeypatch.setattr(db, "_pool", _FakePool(fake_conn))

    result = await db.get_password_hash("ghost")

    assert result is None


async def test_create_user_inserts_with_correct_sql_and_params(monkeypatch, fake_conn):
    monkeypatch.setattr(db, "_pool", _FakePool(fake_conn))

    await db.create_user("bob", "hashed-pw")

    fake_conn.execute.assert_awaited_once()
    query, *params = fake_conn.execute.call_args.args
    assert "INSERT INTO file2md_users" in query
    assert "ON CONFLICT (username)" in query  # đổi mật khẩu = upsert, không lỗi duplicate key
    assert params == ["bob", "hashed-pw"]


def test_get_pool_raises_runtime_error_when_not_initialized(monkeypatch):
    monkeypatch.setattr(db, "_pool", None)

    with pytest.raises(RuntimeError):
        db.get_pool()


def test_get_pool_returns_pool_when_initialized(monkeypatch, fake_conn):
    pool = _FakePool(fake_conn)
    monkeypatch.setattr(db, "_pool", pool)

    assert db.get_pool() is pool
