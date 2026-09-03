import os
from typing import Optional

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE_NAME = "fm_session"
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE_SECONDS", str(7 * 24 * 3600)))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") not in ("0", "false", "False")


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("SESSION_SECRET_KEY")
    if not secret:
        raise RuntimeError("Thiếu biến môi trường SESSION_SECRET_KEY.")
    return URLSafeTimedSerializer(secret, salt="file2md-session")  # secret-allow: salt là namespace string công khai, không phải secret


def create_session_token(username: str) -> str:
    return _serializer().dumps({"username": username})


def verify_session_token(token: str) -> Optional[str]:
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("username")


def _password_bytes(password: str) -> bytes:
    # bcrypt raise ValueError với input >72 byte (thay vì tự cắt) — cắt tay trước khi
    # đưa vào bcrypt, tránh ValueError lọt ra thành 500 (tạo oracle user-enumeration).
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(_password_bytes(password), password_hash.encode("utf-8"))
