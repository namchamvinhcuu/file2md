"""Test endpoint /api/convert cho nhánh paste-text + provider (LLM reformat optional).

Mock ở nơi DÙNG (main.reformat_to_markdown, tên đã bind vào namespace main.py qua
`from llm_providers import reformat_to_markdown`) — KHÔNG patch llm_providers.reformat_to_markdown,
vì main.py giữ reference riêng sau import, patch sai path sẽ không có tác dụng.
"""

import main as main_module
from llm_providers import LLMReformatError


def test_convert_provider_none_is_passthrough_regression(client):
    """Regression: provider='none' phải xử lý y hệt hành vi CŨ trước khi có LLM reformat —
    text trả về nguyên văn, không qua transform nào."""
    text = "Xin chào, đây là *test* tiếng Việt có dấu."

    resp = client.post("/api/convert", data={"text": text, "provider": "none"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["markdown"] == text
    assert body["filename"] == "converted.md"


def test_convert_no_provider_field_defaults_to_passthrough(client):
    """Không truyền field provider (client cũ chưa biết field mới) → default 'none' → passthrough."""
    resp = client.post("/api/convert", data={"text": "plain text, no provider field"})

    assert resp.status_code == 200
    assert resp.json()["markdown"] == "plain text, no provider field"


def test_convert_invalid_provider_returns_400_not_crash(client):
    resp = client.post("/api/convert", data={"text": "hello", "provider": "foo"})

    assert resp.status_code == 400
    assert "foo" in resp.json()["detail"]


def test_convert_llm_reformat_error_returns_502_generic_message_not_500(client, monkeypatch):
    """LLMReformatError từ tầng provider → endpoint phải trả 502, KHÔNG phải 500 generic.
    Message trả về client là GENERIC (không lộ chi tiết lỗi nội bộ như tên biến env/API key) —
    chi tiết thật chỉ đi vào logger.exception phía server."""

    async def _boom(text, provider):
        raise LLMReformatError("Thiếu biến môi trường ANTHROPIC_API_KEY trên server.")

    monkeypatch.setattr(main_module, "reformat_to_markdown", _boom)

    resp = client.post("/api/convert", data={"text": "hello", "provider": "anthropic"})

    assert resp.status_code == 502
    assert resp.status_code != 500
    detail = resp.json()["detail"]
    assert detail  # có message, không rỗng
    assert "ANTHROPIC_API_KEY" not in detail  # KHÔNG lộ chi tiết lỗi nội bộ ra client


def test_convert_llm_success_returns_reformatted_markdown_unicode(client, monkeypatch):
    """Nhánh LLM thành công, mock trả unicode/tiếng Việt → response JSON phải giữ nguyên,
    không bị mangled (mojibake / mất dấu / lỗi encode)."""
    reformatted = "# Tiêu đề\n\nNội dung có dấu: café, naïve, 日本語, emoji 🎉."

    async def _fake(text, provider):
        assert provider == "gemini"
        assert text == "raw đầu vào"
        return reformatted

    monkeypatch.setattr(main_module, "reformat_to_markdown", _fake)

    resp = client.post("/api/convert", data={"text": "raw đầu vào", "provider": "gemini"})

    assert resp.status_code == 200
    assert resp.json()["markdown"] == reformatted


def test_convert_empty_text_and_no_file_returns_400(client):
    """Guard sẵn có (không phải feature mới) — giữ nguyên qua regression khi thêm field provider."""
    resp = client.post("/api/convert", data={"text": "   "})

    assert resp.status_code == 400


def test_convert_text_too_long_for_llm_returns_413_without_calling_provider(client, monkeypatch):
    """Text vượt LLM_MAX_TEXT_LENGTH khi có provider → 413, và KHÔNG được gọi provider
    (chặn sớm trước khi tốn API call thật cho text quá dài)."""
    called = False

    async def _fake(text, provider):
        nonlocal called
        called = True
        return "should not reach here"

    monkeypatch.setattr(main_module, "reformat_to_markdown", _fake)
    too_long_text = "a" * (main_module.LLM_MAX_TEXT_LENGTH + 1)

    resp = client.post("/api/convert", data={"text": too_long_text, "provider": "anthropic"})

    assert resp.status_code == 413
    assert called is False


def test_convert_text_at_max_length_still_calls_provider(client, monkeypatch):
    """Boundary: đúng bằng LLM_MAX_TEXT_LENGTH (KHÔNG vượt) → vẫn cho qua, gọi provider bình thường."""

    async def _fake(text, provider):
        return "# reformatted"

    monkeypatch.setattr(main_module, "reformat_to_markdown", _fake)
    exact_length_text = "a" * main_module.LLM_MAX_TEXT_LENGTH

    resp = client.post("/api/convert", data={"text": exact_length_text, "provider": "anthropic"})

    assert resp.status_code == 200
    assert resp.json()["markdown"] == "# reformatted"


def test_convert_text_too_long_but_provider_none_is_unaffected(client):
    """Guard độ dài chỉ áp cho nhánh LLM — provider='none' (passthrough) không giới hạn độ dài."""
    long_text = "a" * (main_module.LLM_MAX_TEXT_LENGTH + 1)

    resp = client.post("/api/convert", data={"text": long_text, "provider": "none"})

    assert resp.status_code == 200
    assert resp.json()["markdown"] == long_text
