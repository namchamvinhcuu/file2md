import pytest
from fastapi.testclient import TestClient

import auth
import main as main_module
import temp_files


@pytest.fixture(autouse=True)
def _isolated_relay_storage(tmp_path, monkeypatch):
    """Cách ly storage relay tạm (backend/temp_files.py) khỏi TEMP_UPLOAD_DIR thật
    (mặc định /tmp/file2md-relay) và khỏi state để lại bởi test khác — mỗi test dùng
    tmp_path riêng của chính nó; dict `_entries` là module-level state, KHÔNG tự reset
    giữa test nên phải clear tay trước/sau."""
    monkeypatch.setattr(temp_files, "TEMP_UPLOAD_DIR", tmp_path)
    temp_files._entries.clear()
    yield
    temp_files._entries.clear()


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    """Xoá API key/model env var LLM (nếu máy dev có .env thật chứa key) trước MỖI test,
    để test không phụ thuộc trạng thái ngoài — test nào cần key thì tự monkeypatch.setenv.
    Autouse hẹp có chủ đích: chỉ xoá đúng 8 biến LLM, không đụng gì khác."""
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _session_secret(monkeypatch):
    """SESSION_SECRET_KEY cố định cho MỌI test (autouse) — auth.create_session_token/
    verify_session_token raise RuntimeError nếu thiếu biến này. Set cứng ở đây để test
    không phụ thuộc .env thật của máy dev (Verify Empirically — máy chưa có DATABASE_URL/
    SESSION_SECRET_KEY thật, test không được cố kết nối gì thật)."""
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret-key-for-pytest-only")


@pytest.fixture
def client():
    """TestClient ĐÃ đăng nhập (cookie session hợp lệ) — dùng cho test cần vượt qua
    AuthMiddleware (vd /api/convert). Tạo token trực tiếp qua auth.create_session_token,
    KHÔNG gọi qua /api/login thật/DB — nhanh, không phụ thuộc DB thật (chưa có trên máy dev).
    KHÔNG dùng `with TestClient(...)` → lifespan (db.init_pool) không trigger, giữ đúng
    hành vi 12/21 test cũ đã verify thực nghiệm trước khi có auth."""
    test_client = TestClient(main_module.app)
    token = auth.create_session_token("test-user")
    test_client.cookies.set(auth.SESSION_COOKIE_NAME, token)
    return test_client


@pytest.fixture
def unauthed_client():
    """TestClient KHÔNG có cookie session — dùng để test hành vi AuthMiddleware khi
    chưa đăng nhập (401 JSON cho /api/*, redirect /login.html cho route khác)."""
    return TestClient(main_module.app)
