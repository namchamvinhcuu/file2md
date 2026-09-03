"""CLI tạo/đổi mật khẩu tài khoản file2md — KHÔNG có route đăng ký public.

Dùng: $VENV_PY -m create_user <username>
"""
import argparse
import asyncio
import getpass

from auth import hash_password
from db import close_pool, create_user, init_pool


async def _run(username: str, password: str) -> None:
    await init_pool()
    try:
        await create_user(username, hash_password(password))
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo/đổi mật khẩu tài khoản file2md")
    parser.add_argument("username")
    args = parser.parse_args()

    password = getpass.getpass("Mật khẩu: ")
    confirm = getpass.getpass("Nhập lại mật khẩu: ")
    if password != confirm:
        raise SystemExit("Mật khẩu nhập lại không khớp.")

    asyncio.run(_run(args.username, password))
    print(f"Đã tạo/cập nhật tài khoản '{args.username}'.")  # log-allow: CLI stdout, không phải service log


if __name__ == "__main__":
    main()
