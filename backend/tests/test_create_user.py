"""Test backend/create_user.py — CLI tạo/đổi mật khẩu tài khoản, mock hết
init_pool/close_pool/create_user/hash_password (KHÔNG kết nối Postgres thật).

create_user.py dùng `from auth import hash_password` / `from db import close_pool,
create_user, init_pool` — bind tên riêng trong namespace của chính nó, KHÔNG phải
`auth.hash_password`/`db.create_user`. Patch đúng path DÙNG nghĩa là patch attribute
trên module create_user (import as create_user_module để tránh trùng tên với hàm
`create_user` được import từ db.py).
"""

from unittest.mock import AsyncMock

import create_user as create_user_module


async def test_run_hashes_password_and_creates_user(monkeypatch):
    monkeypatch.setattr(create_user_module, "init_pool", AsyncMock())
    monkeypatch.setattr(create_user_module, "close_pool", AsyncMock())
    create_user_mock = AsyncMock()
    monkeypatch.setattr(create_user_module, "create_user", create_user_mock)

    await create_user_module._run("alice", "s3cret")

    create_user_mock.assert_awaited_once()
    username, password_hash = create_user_mock.call_args.args
    assert username == "alice"
    assert password_hash != "s3cret"  # phải hash, KHÔNG lưu plaintext  # secret-allow: fake test password, không phải secret thật


async def test_run_calls_init_pool_before_and_close_pool_after(monkeypatch):
    calls = []

    async def _fake_init_pool():
        calls.append("init")

    async def _fake_close_pool():
        calls.append("close")

    async def _fake_create_user(username, password_hash):
        calls.append("create")

    monkeypatch.setattr(create_user_module, "init_pool", _fake_init_pool)
    monkeypatch.setattr(create_user_module, "close_pool", _fake_close_pool)
    monkeypatch.setattr(create_user_module, "create_user", _fake_create_user)

    await create_user_module._run("alice", "s3cret")

    assert calls == ["init", "create", "close"]


async def test_run_closes_pool_even_when_create_user_raises(monkeypatch):
    """`finally: await close_pool()` phải chạy dù create_user lỗi giữa chừng — tránh rò
    connection pool khi thao tác DB thất bại."""
    monkeypatch.setattr(create_user_module, "init_pool", AsyncMock())
    close_pool_mock = AsyncMock()
    monkeypatch.setattr(create_user_module, "close_pool", close_pool_mock)
    monkeypatch.setattr(
        create_user_module, "create_user", AsyncMock(side_effect=RuntimeError("DB lỗi"))
    )

    try:
        await create_user_module._run("alice", "s3cret")
    except RuntimeError:
        pass

    close_pool_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Regression — python-reviewer finding 🔴 Critical: bcrypt raise ValueError với password
# >72 byte. CLI tạo user với password dài KHÔNG được crash.
# ---------------------------------------------------------------------------


async def test_run_with_password_longer_than_72_bytes_does_not_crash(monkeypatch):
    monkeypatch.setattr(create_user_module, "init_pool", AsyncMock())
    monkeypatch.setattr(create_user_module, "close_pool", AsyncMock())
    create_user_mock = AsyncMock()
    monkeypatch.setattr(create_user_module, "create_user", create_user_mock)
    long_password = "p" * 200

    await create_user_module._run("bob", long_password)

    create_user_mock.assert_awaited_once()
    _, password_hash = create_user_mock.call_args.args
    assert password_hash  # hash tạo thành công, không raise
