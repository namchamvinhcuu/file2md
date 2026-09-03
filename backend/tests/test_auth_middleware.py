"""Test AuthMiddleware + /api/login + /api/logout trong backend/main.py.

Mock db.get_password_hash/auth.verify_password tại đúng module định nghĩa — main.py giữ
`import db` / `import auth` (module reference, không phải `from db import get_password_hash`),
nên patch attribute trên module db/auth CHÍNH LÀ patch tại nơi main.py DÙNG
(main.db.get_password_hash là cùng object với db.get_password_hash).
KHÔNG kết nối Postgres thật — mock hoàn toàn.
"""

from unittest.mock import AsyncMock, MagicMock

import auth
import db
import main as main_module


def test_convert_without_cookie_returns_401_json(unauthed_client):
    resp = unauthed_client.post("/api/convert", data={"text": "hello"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Chưa đăng nhập."


def test_root_without_cookie_redirects_to_login(unauthed_client):
    resp = unauthed_client.get("/", follow_redirects=False)

    assert resp.status_code == 307
    assert resp.headers["location"] == "/login.html"


def test_login_html_accessible_without_cookie(unauthed_client):
    resp = unauthed_client.get("/login.html")

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Regression — bug thật phát hiện qua Playwright (Chromium thật, không phải TestClient
# mock): thiếu header Cache-Control khiến browser tự cache theo Last-Modified heuristic và
# phục vụ lại trang cũ (lúc còn đăng nhập) sau logout mà KHÔNG gọi lại server → middleware
# không có cơ hội chạy lại. Fix: mọi response đi qua nhánh đã-authenticated (ngoài
# PUBLIC_PATHS) phải có "Cache-Control: no-store".
# ---------------------------------------------------------------------------


def test_root_response_after_login_has_no_store_cache_control(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"


def test_convert_response_after_login_has_no_store_cache_control(client):
    resp = client.post("/api/convert", data={"text": "hello"})

    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"


def test_login_html_public_path_still_accessible_regardless_of_cache_control(unauthed_client):
    """PUBLIC_PATHS (login.html/api/login) KHÔNG đi qua nhánh gắn Cache-Control (không có
    dữ liệu nhạy cảm, không bắt buộc header này) — test CHỈ xác nhận route vẫn truy cập
    bình thường, KHÔNG assert ngược 'phải thiếu header' (tránh assert nhầm chiều)."""
    resp = unauthed_client.get("/login.html")

    assert resp.status_code == 200


def test_api_login_endpoint_not_blocked_by_middleware_even_with_bad_creds(unauthed_client, monkeypatch):
    """/api/login nằm trong PUBLIC_PATHS — request PHẢI chạm tới route handler (401 do sai
    creds), KHÔNG bị middleware chặn sớm (mà middleware chặn thì cũng ra 401 nhưng message
    khác/không set-cookie logic đúng route) — verify bằng cách mock để phân biệt rõ."""
    monkeypatch.setattr(db, "get_password_hash", AsyncMock(return_value=None))

    resp = unauthed_client.post("/api/login", data={"username": "ghost", "password": "x"})  # secret-allow: fake test credential, không phải secret thật

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Sai tên đăng nhập hoặc mật khẩu."


def test_login_correct_credentials_returns_200_and_sets_session_cookie(unauthed_client, monkeypatch):
    monkeypatch.setattr(db, "get_password_hash", AsyncMock(return_value="hashed-pw"))
    monkeypatch.setattr(auth, "verify_password", MagicMock(return_value=True))

    resp = unauthed_client.post("/api/login", data={"username": "alice", "password": "correct"})  # secret-allow: fake test credential, không phải secret thật

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert auth.SESSION_COOKIE_NAME in resp.cookies


def test_login_wrong_password_returns_401(unauthed_client, monkeypatch):
    monkeypatch.setattr(db, "get_password_hash", AsyncMock(return_value="hashed-pw"))
    monkeypatch.setattr(auth, "verify_password", MagicMock(return_value=False))

    resp = unauthed_client.post("/api/login", data={"username": "alice", "password": "wrong"})  # secret-allow: fake test credential, không phải secret thật

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Sai tên đăng nhập hoặc mật khẩu."
    assert auth.SESSION_COOKIE_NAME not in resp.cookies


def test_login_nonexistent_user_still_calls_verify_password_for_constant_time(unauthed_client, monkeypatch):
    """Regression cho finding 🟠 timing side-channel (python-reviewer) — username KHÔNG tồn
    tại vẫn PHẢI gọi auth.verify_password (so với main._DUMMY_PASSWORD_HASH thay vì short-
    circuit), để thời gian phản hồi không tiết lộ user có tồn tại hay không."""
    monkeypatch.setattr(db, "get_password_hash", AsyncMock(return_value=None))
    verify_password_mock = MagicMock(return_value=False)
    monkeypatch.setattr(auth, "verify_password", verify_password_mock)

    resp = unauthed_client.post("/api/login", data={"username": "ghost", "password": "whatever"})  # secret-allow: fake test credential, không phải secret thật

    assert resp.status_code == 401
    verify_password_mock.assert_called_once()
    called_password, called_hash = verify_password_mock.call_args.args
    assert called_password == "whatever"
    assert called_hash == main_module._DUMMY_PASSWORD_HASH  # so với dummy hash, KHÔNG phải None


# ---------------------------------------------------------------------------
# Regression — python-reviewer finding 🔴 Critical: bcrypt raise ValueError với password
# >72 byte (không tự cắt) → nếu không xử lý, /api/login sẽ 500 thay vì 401, tạo oracle
# user-enumeration rõ hơn cả timing. auth._password_bytes() đã cắt về 72 byte.
# ---------------------------------------------------------------------------


def test_login_with_wrong_password_longer_than_72_bytes_returns_401_not_500(unauthed_client, monkeypatch):
    """Dùng auth.verify_password/hash_password THẬT (không mock) — verify đúng finding:
    password dài không làm bcrypt raise ValueError lọt thành 500."""
    real_hash = auth.hash_password("correct-password")
    monkeypatch.setattr(db, "get_password_hash", AsyncMock(return_value=real_hash))
    long_wrong_password = "x" * 200  # 200 byte ASCII, vượt xa giới hạn 72 byte của bcrypt

    resp = unauthed_client.post(
        "/api/login", data={"username": "alice", "password": long_wrong_password}
    )

    assert resp.status_code == 401
    assert resp.status_code != 500
    assert resp.json()["detail"] == "Sai tên đăng nhập hoặc mật khẩu."


def test_login_with_correct_password_longer_than_72_bytes_succeeds(unauthed_client, monkeypatch):
    long_password = "y" * 200
    real_hash = auth.hash_password(long_password)
    monkeypatch.setattr(db, "get_password_hash", AsyncMock(return_value=real_hash))

    resp = unauthed_client.post("/api/login", data={"username": "alice", "password": long_password})

    assert resp.status_code == 200
    assert auth.SESSION_COOKIE_NAME in resp.cookies


def test_login_nonexistent_user_with_password_longer_than_72_bytes_returns_401_not_500(
    unauthed_client, monkeypatch
):
    """Nhánh constant-time (verify_password so với _DUMMY_PASSWORD_HASH) cũng phải an toàn
    với password dài — cả 2 finding cộng lại không được crash 500."""
    monkeypatch.setattr(db, "get_password_hash", AsyncMock(return_value=None))
    long_password = "z" * 200

    resp = unauthed_client.post("/api/login", data={"username": "ghost", "password": long_password})

    assert resp.status_code == 401
    assert resp.status_code != 500


def test_login_wrong_password_and_nonexistent_user_give_identical_response(unauthed_client, monkeypatch):
    """Chống user enumeration: response (status + body) PHẢI giống hệt nhau giữa 'sai mật
    khẩu' và 'user không tồn tại' — không được lộ chi tiết nào phân biệt 2 case."""
    monkeypatch.setattr(db, "get_password_hash", AsyncMock(return_value="hashed-pw"))
    monkeypatch.setattr(auth, "verify_password", MagicMock(return_value=False))
    resp_wrong_pw = unauthed_client.post("/api/login", data={"username": "alice", "password": "wrong"})  # secret-allow: fake test credential, không phải secret thật

    monkeypatch.setattr(db, "get_password_hash", AsyncMock(return_value=None))
    resp_no_user = unauthed_client.post("/api/login", data={"username": "ghost", "password": "whatever"})  # secret-allow: fake test credential, không phải secret thật

    assert resp_wrong_pw.status_code == resp_no_user.status_code == 401
    assert resp_wrong_pw.json() == resp_no_user.json()


def test_logout_clears_cookie_and_subsequent_request_blocked(unauthed_client, monkeypatch):
    """Round-trip THẬT qua /api/login (không inject cookie tay) để jar httpx2 của TestClient
    theo dõi domain/cookie đúng như request thật, rồi verify /api/logout xoá cookie.

    ⚠ Verify Empirically finding: TestClient gọi qua scheme "http://testserver" — cookie
    `Secure=True` (auth.COOKIE_SECURE mặc định True) sẽ KHÔNG được httpx2's cookie jar
    (http.cookiejar policy `return_ok_secure`) gửi lại ở request sau, dù server set đúng.
    Đây CHÍNH LÀ lý do `.env.example` có dòng `COOKIE_SECURE=false` cho "test local qua http"
    — set false ở đây để test round-trip qua TestClient hoạt động đúng như production (https
    thật thì COOKIE_SECURE=1 mặc định, không tắt)."""
    monkeypatch.setattr(auth, "COOKIE_SECURE", False)
    monkeypatch.setattr(db, "get_password_hash", AsyncMock(return_value="hashed-pw"))
    monkeypatch.setattr(auth, "verify_password", MagicMock(return_value=True))

    login_resp = unauthed_client.post("/api/login", data={"username": "alice", "password": "pw"})  # secret-allow: fake test credential, không phải secret thật
    assert login_resp.status_code == 200

    resp = unauthed_client.post("/api/convert", data={"text": "trước logout vẫn qua được"})
    assert resp.status_code == 200

    logout_resp = unauthed_client.post("/api/logout")
    assert logout_resp.status_code == 200

    resp_after = unauthed_client.post("/api/convert", data={"text": "sau logout phải bị chặn"})
    assert resp_after.status_code == 401


def test_logout_response_sends_cookie_expiry_header_regardless_of_secure_flag(client):
    """Bổ sung assertion độc lập với cơ chế jar (không phụ thuộc COOKIE_SECURE/scheme) —
    verify trực tiếp response header PHẢI có Max-Age=0 cho đúng tên cookie session."""
    resp = client.post("/api/logout")

    assert resp.status_code == 200
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert auth.SESSION_COOKIE_NAME in set_cookie_header
    assert "Max-Age=0" in set_cookie_header
