import os
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS file2md_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def init_pool() -> None:
    global _pool
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("Thiếu biến môi trường DATABASE_URL.")
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        await conn.execute(CREATE_USERS_TABLE)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool chưa khởi tạo.")
    return _pool


async def get_password_hash(username: str) -> Optional[str]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM file2md_users WHERE username = $1", username
        )
        return row["password_hash"] if row else None


async def create_user(username: str, password_hash: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO file2md_users (username, password_hash) VALUES ($1, $2) "
            "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash",
            username,
            password_hash,
        )
