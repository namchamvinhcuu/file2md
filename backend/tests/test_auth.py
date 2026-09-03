"""Test backend/auth.py — hash/verify password (bcrypt) + tạo/verify session token
(itsdangerous). Unit test thuần, không cần DB/network.

SESSION_SECRET_KEY đã được set qua fixture autouse `_session_secret` (conftest.py).
"""

import pytest

import auth


def test_create_and_verify_session_token_roundtrip():
    token = auth.create_session_token("alice")

    username = auth.verify_session_token(token)

    assert username == "alice"


def test_verify_session_token_expired_returns_none(monkeypatch):
    """Token ký xong nhưng đã quá SESSION_MAX_AGE → verify phải trả None (SignatureExpired).
    Giả lập hết hạn bằng cách patch time.time() ở itsdangerous.timed (nơi TimestampSigner
    lấy timestamp lúc kiểm tra) thay vì sleep thật — nhanh, deterministic."""
    token = auth.create_session_token("alice")
    monkeypatch.setattr(auth, "SESSION_MAX_AGE", 1)
    future_time = __import__("time").time() + 1000  # cách xa mốc ký, chắc chắn vượt max_age=1s
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: future_time)

    assert auth.verify_session_token(token) is None


def test_verify_session_token_tampered_returns_none():
    """Sửa 1 ký tự GIỮA token → chữ ký không còn khớp → None.
    Tamper ở GIỮA (không phải ký tự cuối cùng) để tránh rủi ro lý thuyết: ký tự cuối của
    base64 URL-safe có thể rơi vào nhóm bit padding không dùng hết, vài giá trị thay thế
    có thể decode ra cùng byte gốc (false-negative hiếm gặp) — vị trí giữa luôn nằm trọn
    trong 1 group base64 đầy đủ nên đổi ký tự chắc chắn đổi byte thật."""
    token = auth.create_session_token("alice")
    mid = len(token) // 2
    original_char = token[mid]
    replacement = "A" if original_char != "A" else "B"
    tampered = token[:mid] + replacement + token[mid + 1 :]

    assert tampered != token
    assert auth.verify_session_token(tampered) is None


def test_verify_session_token_garbage_input_returns_none():
    assert auth.verify_session_token("not-a-valid-token-at-all") is None


def test_create_session_token_requires_secret_key(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError):
        auth.create_session_token("alice")


def test_verify_session_token_requires_secret_key(monkeypatch):
    token = auth.create_session_token("alice")
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError):
        auth.verify_session_token(token)


def test_hash_password_and_verify_password_correct():
    password_hash = auth.hash_password("s3cr3t-passw0rd!")

    assert auth.verify_password("s3cr3t-passw0rd!", password_hash) is True


def test_verify_password_wrong_password_returns_false():
    password_hash = auth.hash_password("s3cr3t-passw0rd!")

    assert auth.verify_password("wrong-password", password_hash) is False


def test_hash_password_does_not_store_plaintext():
    password_hash = auth.hash_password("s3cr3t-passw0rd!")

    assert "s3cr3t-passw0rd!" not in password_hash


# ---------------------------------------------------------------------------
# Regression — python-reviewer finding 🔴 Critical: bcrypt.checkpw/hashpw raise
# ValueError với input >72 byte (không tự cắt) → nếu không xử lý, /api/login sẽ 500
# thay vì 401, tạo oracle user-enumeration rõ hơn cả timing side-channel.
# auth._password_bytes() cắt về 72 byte TRƯỚC khi đưa vào bcrypt cho cả hash lẫn verify.
# ---------------------------------------------------------------------------


def test_hash_and_verify_password_longer_than_72_bytes_ascii_does_not_crash():
    long_password = "a" * 100  # 100 byte ASCII, vượt giới hạn 72 byte của bcrypt  # secret-allow: fake test password, không phải secret thật

    password_hash = auth.hash_password(long_password)

    assert auth.verify_password(long_password, password_hash) is True


def test_hash_and_verify_password_longer_than_72_bytes_unicode_does_not_crash():
    # Mỗi ký tự có dấu tiếng Việt chiếm 2-3 byte UTF-8 → lặp lại để chắc chắn vượt 72 byte
    long_unicode_password = "mật khẩu tiếng Việt có dấu rất dài để vượt quá giới hạn 72 byte" * 2  # secret-allow: fake test password, không phải secret thật
    assert len(long_unicode_password.encode("utf-8")) > 72

    password_hash = auth.hash_password(long_unicode_password)

    assert auth.verify_password(long_unicode_password, password_hash) is True


def test_verify_password_wrong_long_password_returns_false_not_raises():
    """Sai mật khẩu (>72 byte) vẫn phải trả False gọn gàng — KHÔNG raise ValueError."""
    password_hash = auth.hash_password("a" * 100)

    assert auth.verify_password("b" * 100, password_hash) is False
