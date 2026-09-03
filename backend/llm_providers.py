import logging
import os

import aiohttp
from anthropic import AsyncAnthropic
from google import genai
from google.genai import errors as genai_errors
from google.genai.types import GenerateContentConfig, HttpOptions
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT_MS = int(os.environ.get("GEMINI_TIMEOUT_MS", "60000"))

SYSTEM_PROMPT = (
    "Bạn là công cụ chuyển văn bản thô sang Markdown có cấu trúc. "
    "Nhận diện heading, danh sách, đoạn văn, chữ in đậm/nghiêng từ ngữ cảnh và định dạng lại "
    "bằng cú pháp Markdown chuẩn. GIỮ NGUYÊN toàn bộ nội dung và ngôn ngữ gốc — không thêm, "
    "bớt, dịch hay tóm tắt. Chỉ trả về nội dung Markdown, không giải thích thêm."
)

PROVIDERS = {"anthropic", "openai", "gemini", "deepseek"}


class LLMReformatError(RuntimeError):
    """Raised when an LLM provider fails to reformat text."""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise LLMReformatError(f"Thiếu biến môi trường {name} trên server.")
    return value


async def reformat_to_markdown(text: str, provider: str) -> str:
    if provider == "anthropic":
        return await _via_anthropic(text)
    if provider == "openai":
        return await _via_openai_compatible(
            text,
            api_key=_require_env("OPENAI_API_KEY"),
            base_url=None,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        )
    if provider == "deepseek":
        return await _via_openai_compatible(
            text,
            api_key=_require_env("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        )
    if provider == "gemini":
        return await _via_gemini(text)
    raise LLMReformatError(f"Provider không hỗ trợ: {provider}")


async def _via_anthropic(text: str) -> str:
    api_key = _require_env("ANTHROPIC_API_KEY")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    try:
        async with AsyncAnthropic(api_key=api_key) as client:
            message = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
            )
    except Exception as exc:
        logger.exception("Anthropic API call failed (model=%s)", model)
        raise LLMReformatError(f"Lỗi gọi Anthropic API: {exc}") from exc
    try:
        return message.content[0].text
    except (IndexError, AttributeError) as exc:
        logger.exception("Anthropic response có shape không như mong đợi (model=%s)", model)
        raise LLMReformatError(f"Phản hồi Anthropic API không đúng định dạng mong đợi: {exc}") from exc


async def _via_openai_compatible(text: str, *, api_key: str, base_url, model: str) -> str:
    try:
        async with AsyncOpenAI(api_key=api_key, base_url=base_url) as client:
            completion = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
            )
    except Exception as exc:
        logger.exception("API call failed (model=%s)", model)
        raise LLMReformatError(f"Lỗi gọi API ({model}): {exc}") from exc
    try:
        return completion.choices[0].message.content or ""
    except (IndexError, AttributeError) as exc:
        logger.exception("Response có shape không như mong đợi (model=%s)", model)
        raise LLMReformatError(f"Phản hồi API ({model}) không đúng định dạng mong đợi: {exc}") from exc


async def _via_gemini(text: str) -> str:
    api_key = _require_env("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key, http_options=HttpOptions(timeout=GEMINI_TIMEOUT_MS))
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=text,
            config=GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        )
        return (resp.text or "").strip()
    except (genai_errors.APIError, aiohttp.ClientError) as exc:
        logger.exception("Gemini API call failed (model=%s)", model)
        raise LLMReformatError(f"Lỗi gọi Gemini API: {exc}") from exc
    finally:
        await client.aio.aclose()
