"""Test llm_providers.reformat_to_markdown — mock hết 4 SDK client, KHÔNG gọi API thật.

Patch tại nơi llm_providers.py DÙNG tên (llm_providers.AsyncAnthropic / .AsyncOpenAI /
.genai.Client), không phải nơi SDK định nghĩa — đúng path để patch có tác dụng.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import errors as genai_errors

import llm_providers
from llm_providers import LLMReformatError, reformat_to_markdown


class _AsyncClientCM:
    """Giả lập `async with SomeAsyncClient(...) as client:` — SDK Anthropic/OpenAI đều là
    async context manager thật (verify: AsyncAnthropic/AsyncOpenAI có __aenter__/__aexit__)."""

    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc_info):
        return False


# ---------------------------------------------------------------------------
# Happy path — mock từng provider trả về text giả, verify reformat_to_markdown
# trả ĐÚNG text đó (bao gồm unicode/tiếng Việt, không bị mangled).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_via_anthropic_returns_mocked_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-anthropic")

    fake_message = MagicMock()
    fake_message.content = [MagicMock(text="# Tiêu đề tiếng Việt từ Anthropic — café, 日本語")]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=fake_message)

    monkeypatch.setattr(llm_providers, "AsyncAnthropic", lambda **kw: _AsyncClientCM(mock_client))

    result = await reformat_to_markdown("raw text", "anthropic")

    assert result == "# Tiêu đề tiếng Việt từ Anthropic — café, 日本語"
    mock_client.messages.create.assert_awaited_once()
    _, kwargs = mock_client.messages.create.call_args
    assert kwargs["messages"] == [{"role": "user", "content": "raw text"}]


@pytest.mark.asyncio
async def test_via_openai_returns_mocked_text(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")

    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="# Markdown từ OpenAI"))]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_completion)

    captured = {}

    def _ctor(**kwargs):
        captured.update(kwargs)
        return _AsyncClientCM(mock_client)

    monkeypatch.setattr(llm_providers, "AsyncOpenAI", _ctor)

    result = await reformat_to_markdown("raw text", "openai")

    assert result == "# Markdown từ OpenAI"
    assert captured["base_url"] is None  # openai dùng endpoint mặc định của SDK


@pytest.mark.asyncio
async def test_via_deepseek_uses_openai_compatible_client_with_custom_base_url(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")

    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="# Markdown từ DeepSeek"))]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_completion)

    captured = {}

    def _ctor(**kwargs):
        captured.update(kwargs)
        return _AsyncClientCM(mock_client)

    monkeypatch.setattr(llm_providers, "AsyncOpenAI", _ctor)

    result = await reformat_to_markdown("raw text", "deepseek")

    assert result == "# Markdown từ DeepSeek"
    assert captured["base_url"] == "https://api.deepseek.com"


@pytest.mark.asyncio
async def test_via_gemini_returns_mocked_text_unicode_stripped(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test-gemini")

    fake_resp = MagicMock()
    fake_resp.text = "  # Tiêu đề tiếng Việt có dấu — emoji 🎉  "
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=fake_resp)
    mock_client.aio.aclose = AsyncMock()

    monkeypatch.setattr(llm_providers.genai, "Client", lambda **kw: mock_client)

    result = await reformat_to_markdown("raw text", "gemini")

    assert result == "# Tiêu đề tiếng Việt có dấu — emoji 🎉"  # code tự .strip()
    mock_client.aio.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Provider không hợp lệ ở tầng module (endpoint đã chặn ở PROVIDERS set trước khi
# gọi tới đây, nhưng reformat_to_markdown tự nó cũng phải an toàn nếu gọi trực tiếp).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reformat_to_markdown_unknown_provider_raises_llm_reformat_error():
    with pytest.raises(LLMReformatError, match="foo"):
        await reformat_to_markdown("raw text", "foo")


# ---------------------------------------------------------------------------
# Thiếu API key → LLMReformatError với message rõ ràng, KHÔNG traceback lạ.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,env_name",
    [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
    ],
)
async def test_reformat_missing_api_key_raises_clear_llm_reformat_error(provider, env_name):
    # _clear_llm_env (conftest, autouse) đã xoá hết 8 biến trước mỗi test — không cần setenv gì.
    with pytest.raises(LLMReformatError) as exc_info:
        await reformat_to_markdown("raw text", provider)

    assert env_name in str(exc_info.value)


# ---------------------------------------------------------------------------
# SDK raise exception (API lỗi / network) → phải được wrap thành LLMReformatError,
# KHÔNG để exception gốc của SDK lọt ra ngoài.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_via_anthropic_sdk_error_wrapped(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("connection reset by peer"))
    monkeypatch.setattr(llm_providers, "AsyncAnthropic", lambda **kw: _AsyncClientCM(mock_client))

    with pytest.raises(LLMReformatError) as exc_info:
        await reformat_to_markdown("raw text", "anthropic")

    assert "connection reset by peer" in str(exc_info.value)


@pytest.mark.asyncio
async def test_via_openai_sdk_error_wrapped(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("HTTP 500"))
    monkeypatch.setattr(llm_providers, "AsyncOpenAI", lambda **kw: _AsyncClientCM(mock_client))

    with pytest.raises(LLMReformatError) as exc_info:
        await reformat_to_markdown("raw text", "openai")

    assert "HTTP 500" in str(exc_info.value)


@pytest.mark.asyncio
async def test_via_gemini_api_error_wrapped(monkeypatch):
    """genai_errors.APIError (base class của ClientError/ServerError SDK thật raise cho lỗi
    API 4xx/5xx) — case NẰM TRONG except clause hiện tại của _via_gemini, phải được wrap."""
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test")

    mock_client = MagicMock()
    api_error = genai_errors.APIError(500, {"error": {"message": "internal error"}})
    mock_client.aio.models.generate_content = AsyncMock(side_effect=api_error)
    mock_client.aio.aclose = AsyncMock()
    monkeypatch.setattr(llm_providers.genai, "Client", lambda **kw: mock_client)

    with pytest.raises(LLMReformatError) as exc_info:
        await reformat_to_markdown("raw text", "gemini")

    assert "internal error" in str(exc_info.value) or "500" in str(exc_info.value)
    mock_client.aio.aclose.assert_awaited_once()  # finally vẫn phải chạy dù lỗi

# NOTE (không phải test — để KHÔNG làm suite đỏ theo yêu cầu "verify ALL PASS"):
# _via_gemini hiện chỉ `except (genai_errors.APIError, aiohttp.ClientError)`, nhưng SDK
# google-genai dùng httpx làm transport thật (site-packages/google/genai/client.py import
# httpx, KHÔNG dùng aiohttp cho request) → lỗi network thật kiểu httpx.ConnectError SẼ
# KHÔNG bị bắt và lọt raw ra ngoài thay vì LLMReformatError. Verify thực nghiệm (script rời,
# không nằm trong suite): mock generate_content side_effect=httpx.ConnectError("boom") ->
# kết quả quan sát được là httpx.ConnectError lọt raw, KHÔNG phải LLMReformatError.
# Đây là bug thật trong code, ngoài scope sửa của test-writer — báo lại ở report cuối cho
# main/reviewer quyết định (đề xuất: đổi aiohttp.ClientError thành httpx.HTTPError, hoặc
# except Exception rộng như 2 nhánh anthropic/openai).
