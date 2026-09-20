"""Test endpoint relay file tạm public trong backend/main.py:
  - POST /api/temp-upload (yêu cầu cookie session — dùng lại AuthMiddleware sẵn có)
  - GET  /dl/{token}/{filename} (PUBLIC, không qua login — PUBLIC_PREFIXES)

Storage đã cách ly khỏi TEMP_UPLOAD_DIR thật qua fixture autouse `_isolated_relay_storage`
(conftest.py). `client`/`unauthed_client` cũng từ conftest.py (pattern có sẵn cho auth).
"""

import asyncio
import logging

import pytest
from fastapi import HTTPException

import main as main_module
import temp_files


# ---------------------------------------------------------------------------
# POST /api/temp-upload — auth gate (dùng lại AuthMiddleware, không có auth riêng)
# ---------------------------------------------------------------------------


def test_temp_upload_without_cookie_returns_401_json(unauthed_client):
    resp = unauthed_client.post(
        "/api/temp-upload",
        files={"file": ("report.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Chưa đăng nhập."


# ---------------------------------------------------------------------------
# POST /api/temp-upload — happy path
# ---------------------------------------------------------------------------


def test_temp_upload_with_valid_pdf_returns_200_with_dl_url(client, monkeypatch):
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "")  # deterministic: dùng request.url_for
    resp = client.post(
        "/api/temp-upload",
        files={"file": ("report.pdf", b"%PDF-1.4 fake content", "application/pdf")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "/dl/" in body["url"]
    assert body["url"].endswith(".pdf")
    assert body["expires_in_seconds"] == temp_files.TEMP_UPLOAD_TTL_SECONDS


def test_temp_upload_with_public_base_url_uses_it_as_prefix(client, monkeypatch):
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "https://relay.example.com")
    resp = client.post(
        "/api/temp-upload",
        files={"file": ("report.pdf", b"content", "application/pdf")},
    )

    assert resp.status_code == 200
    url = resp.json()["url"]
    assert url.startswith("https://relay.example.com/dl/")
    assert url.endswith(".pdf")


def test_temp_upload_saved_file_is_downloadable_via_returned_url(client, monkeypatch):
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "")
    upload_resp = client.post(
        "/api/temp-upload",
        files={"file": ("report.pdf", b"conteudo-do-pdf", "application/pdf")},
    )
    assert upload_resp.status_code == 200
    url = upload_resp.json()["url"]
    path = "/" + url.split("://", 1)[1].split("/", 1)[1]  # strip scheme://host, giữ /dl/...

    download_resp = client.get(path)

    assert download_resp.status_code == 200
    assert download_resp.content == b"conteudo-do-pdf"


# ---------------------------------------------------------------------------
# POST /api/temp-upload — validation
# ---------------------------------------------------------------------------


def test_temp_upload_with_txt_extension_returns_400(client):
    resp = client.post(
        "/api/temp-upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert resp.status_code == 400
    assert ".pdf" in resp.json()["detail"]


def test_temp_upload_over_size_limit_returns_413(client, monkeypatch):
    monkeypatch.setattr(main_module, "MAX_RELAY_UPLOAD_BYTES", 10)
    resp = client.post(
        "/api/temp-upload",
        files={"file": ("big.pdf", b"x" * 100, "application/pdf")},
    )

    assert resp.status_code == 413


def test_temp_upload_at_exact_size_limit_still_succeeds(client, monkeypatch):
    monkeypatch.setattr(main_module, "MAX_RELAY_UPLOAD_BYTES", 10)
    resp = client.post(
        "/api/temp-upload",
        files={"file": ("ok.pdf", b"x" * 10, "application/pdf")},
    )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /dl/{token}/{filename} — PUBLIC, không cần cookie
# ---------------------------------------------------------------------------


def test_download_temp_file_valid_token_returns_200_with_no_store_and_body(unauthed_client):
    token, filename = temp_files.save_temp_file("doc.pdf", b"pdf-bytes-content")

    resp = unauthed_client.get(f"/dl/{token}/{filename}")

    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"
    assert resp.content == b"pdf-bytes-content"


def test_download_temp_file_wrong_token_returns_404(unauthed_client):
    temp_files.save_temp_file("doc.pdf", b"content")

    resp = unauthed_client.get("/dl/token-does-not-exist/doc.pdf")

    assert resp.status_code == 404


def test_download_temp_file_wrong_filename_same_token_returns_404(unauthed_client):
    token, _filename = temp_files.save_temp_file("doc.pdf", b"content")

    resp = unauthed_client.get(f"/dl/{token}/wrong-name.pdf")

    assert resp.status_code == 404


def test_download_temp_file_expired_token_returns_404(unauthed_client, monkeypatch):
    monkeypatch.setattr(temp_files, "TEMP_UPLOAD_TTL_SECONDS", -1)
    token, filename = temp_files.save_temp_file("doc.pdf", b"content")

    resp = unauthed_client.get(f"/dl/{token}/{filename}")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Regression — PUBLIC_PREFIXES=("/dl/",) KHÔNG được vô tình nới lỏng AuthMiddleware cho
# route khác ngoài /dl/.
# ---------------------------------------------------------------------------


def test_convert_still_blocked_without_cookie_after_dl_public_prefix_added(unauthed_client):
    resp = unauthed_client.post("/api/convert", data={"text": "hello"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Chưa đăng nhập."


def test_root_still_redirects_to_login_without_cookie_after_dl_public_prefix_added(unauthed_client):
    resp = unauthed_client.get("/", follow_redirects=False)

    assert resp.status_code == 307
    assert resp.headers["location"] == "/login.html"


# ---------------------------------------------------------------------------
# Regression — python-reviewer finding 🟠 "blocking I/O": save_temp_file()/get_temp_file()
# là I/O đồng bộ (mkdir/write_bytes/unlink) — gọi trực tiếp trong handler async sẽ block
# event loop. Fix: bọc qua asyncio.to_thread(). Test verify ĐÚNG hàm blocking được đưa qua
# to_thread (không chỉ tin behavior cuối giống nhau — mutation gỡ to_thread vẫn ra 200/200).
# ---------------------------------------------------------------------------


def test_temp_upload_calls_save_temp_file_via_asyncio_to_thread(client, monkeypatch):
    calls = []
    original_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, *args, **kwargs):
        calls.append(func)
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(main_module.asyncio, "to_thread", _spy_to_thread)

    resp = client.post(
        "/api/temp-upload",
        files={"file": ("report.pdf", b"content", "application/pdf")},
    )

    assert resp.status_code == 200
    assert temp_files.save_temp_file in calls


def test_download_temp_file_calls_get_temp_file_via_asyncio_to_thread(unauthed_client, monkeypatch):
    calls = []
    original_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, *args, **kwargs):
        calls.append(func)
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(main_module.asyncio, "to_thread", _spy_to_thread)
    token, filename = temp_files.save_temp_file("doc.pdf", b"content")

    resp = unauthed_client.get(f"/dl/{token}/{filename}")

    assert resp.status_code == 200
    assert temp_files.get_temp_file in calls


# ---------------------------------------------------------------------------
# Regression — python-reviewer finding 🟠 "đọc theo chunk có giới hạn": trước fix, endpoint
# đọc TOÀN BỘ body (`await file.read()`) rồi mới check size → buffer hết vào RAM trước khi
# reject. Fix: `_read_upload_within_limit()` đọc từng chunk _UPLOAD_CHUNK_SIZE, abort NGAY
# khi total vượt limit — test dưới dùng fake UploadFile để đếm số lần .read() được gọi,
# xác nhận KHÔNG đọc hết toàn bộ chunk khả dụng sau khi đã vượt limit.
# ---------------------------------------------------------------------------


class _FakeUploadFile:
    """Giả lập UploadFile trả về N chunk cỡ _UPLOAD_CHUNK_SIZE — đếm số lần .read() gọi để
    verify _read_upload_within_limit() abort sớm, không đọc hết toàn bộ chunk khả dụng."""

    def __init__(self, total_chunks_available: int):
        self._remaining = total_chunks_available
        self.read_call_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_call_sizes.append(size)
        if self._remaining <= 0:
            return b""
        self._remaining -= 1
        return b"x" * size


async def test_read_upload_within_limit_aborts_before_reading_all_available_chunks():
    chunk_size = main_module._UPLOAD_CHUNK_SIZE
    limit = chunk_size * 2  # vượt limit ngay sau chunk thứ 3 (3MB > 2MB)
    fake_file = _FakeUploadFile(total_chunks_available=10)

    with pytest.raises(HTTPException) as exc_info:
        await main_module._read_upload_within_limit(fake_file, limit)

    assert exc_info.value.status_code == 413
    # Chỉ đọc đủ 3 chunk để phát hiện vượt limit — KHÔNG đọc hết 10 chunk khả dụng.
    assert len(fake_file.read_call_sizes) == 3


async def test_read_upload_within_limit_reads_exactly_to_limit_without_raising():
    chunk_size = main_module._UPLOAD_CHUNK_SIZE
    limit = chunk_size * 2
    fake_file = _FakeUploadFile(total_chunks_available=2)  # đúng bằng limit, không vượt

    content = await main_module._read_upload_within_limit(fake_file, limit)

    assert len(content) == limit


def test_temp_upload_multi_chunk_body_is_correctly_reassembled(client, monkeypatch):
    """Payload vượt 1 chunk (_UPLOAD_CHUNK_SIZE) thật — verify các chunk được join lại đúng
    thứ tự/nội dung (không mất/lặp byte ở ranh giới chunk)."""
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "")
    chunk_size = main_module._UPLOAD_CHUNK_SIZE
    content = (b"A" * chunk_size) + (b"B" * 100)

    resp = client.post(
        "/api/temp-upload",
        files={"file": ("big.pdf", content, "application/pdf")},
    )

    assert resp.status_code == 200
    url = resp.json()["url"]
    path = "/" + url.split("://", 1)[1].split("/", 1)[1]

    download_resp = client.get(path)

    assert download_resp.content == content


# ---------------------------------------------------------------------------
# Regression — python-reviewer finding 🟠 "log warning khi thiếu PUBLIC_BASE_URL": nhánh
# fallback (tự suy URL từ request) có thể sai scheme http/https sau reverse-proxy — phải
# log warning để vận hành biết mà set PUBLIC_BASE_URL, KHÔNG âm thầm chạy.
# ---------------------------------------------------------------------------


def test_temp_upload_without_public_base_url_logs_warning(client, monkeypatch, caplog):
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "")

    with caplog.at_level(logging.WARNING, logger="main"):
        resp = client.post(
            "/api/temp-upload",
            files={"file": ("report.pdf", b"content", "application/pdf")},
        )

    assert resp.status_code == 200
    assert any("PUBLIC_BASE_URL" in record.message for record in caplog.records)


def test_temp_upload_with_public_base_url_does_not_log_warning(client, monkeypatch, caplog):
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "https://relay.example.com")

    with caplog.at_level(logging.WARNING, logger="main"):
        resp = client.post(
            "/api/temp-upload",
            files={"file": ("report.pdf", b"content", "application/pdf")},
        )

    assert resp.status_code == 200
    assert not any("PUBLIC_BASE_URL" in record.message for record in caplog.records)
