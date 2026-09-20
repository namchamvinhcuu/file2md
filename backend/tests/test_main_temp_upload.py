"""Test endpoint relay file tạm public trong backend/main.py:
  - POST /api/temp-upload (yêu cầu cookie session — dùng lại AuthMiddleware sẵn có)
  - GET  /dl/{token}/{filename} (PUBLIC, không qua login — PUBLIC_PREFIXES)

Storage đã cách ly khỏi TEMP_UPLOAD_DIR thật qua fixture autouse `_isolated_relay_storage`
(conftest.py). `client`/`unauthed_client` cũng từ conftest.py (pattern có sẵn cho auth).
"""

import asyncio
import io
import logging

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

import auth
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
        files=[("files", ("report.pdf", b"%PDF-1.4 fake content", "application/pdf"))],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["files"]) == 1
    entry = body["files"][0]
    assert "/dl/" in entry["url"]
    assert entry["url"].endswith(".pdf")
    assert body["expires_in_seconds"] == temp_files.TEMP_UPLOAD_TTL_SECONDS


def test_temp_upload_with_public_base_url_uses_it_as_prefix(client, monkeypatch):
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "https://relay.example.com")
    resp = client.post(
        "/api/temp-upload",
        files=[("files", ("report.pdf", b"content", "application/pdf"))],
    )

    assert resp.status_code == 200
    url = resp.json()["files"][0]["url"]
    assert url.startswith("https://relay.example.com/dl/")
    assert url.endswith(".pdf")


def test_temp_upload_saved_file_is_downloadable_via_returned_url(client, monkeypatch):
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "")
    upload_resp = client.post(
        "/api/temp-upload",
        files=[("files", ("report.pdf", b"conteudo-do-pdf", "application/pdf"))],
    )
    assert upload_resp.status_code == 200
    url = upload_resp.json()["files"][0]["url"]
    path = "/" + url.split("://", 1)[1].split("/", 1)[1]  # strip scheme://host, giữ /dl/...

    download_resp = client.get(path)

    assert download_resp.status_code == 200
    assert download_resp.content == b"conteudo-do-pdf"


# ---------------------------------------------------------------------------
# POST /api/temp-upload — multi-file (2026-09-20, endpoint đổi từ nhận 1 file sang nhận
# NHIỀU file cùng lúc — field `files` lặp lại nhiều lần trong multipart request).
# ---------------------------------------------------------------------------


def test_temp_upload_with_three_valid_files_returns_200_with_three_entries(client, monkeypatch):
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "")
    resp = client.post(
        "/api/temp-upload",
        files=[
            ("files", ("a.pdf", b"content-a", "application/pdf")),
            ("files", ("b.pdf", b"content-b", "application/pdf")),
            ("files", ("c.pdf", b"content-c", "application/pdf")),
        ],
    )

    assert resp.status_code == 200
    body = resp.json()
    entries = body["files"]
    assert len(entries) == 3
    # Mỗi entry có filename + url RIÊNG (không trùng nhau).
    assert {e["filename"] for e in entries} == {"a.pdf", "b.pdf", "c.pdf"}
    assert len({e["url"] for e in entries}) == 3

    # Cả 3 file đều download được qua URL riêng của nó, đúng nội dung tương ứng.
    expected_content_by_name = {"a.pdf": b"content-a", "b.pdf": b"content-b", "c.pdf": b"content-c"}
    for entry in entries:
        path = "/" + entry["url"].split("://", 1)[1].split("/", 1)[1]
        download_resp = client.get(path)
        assert download_resp.status_code == 200
        assert download_resp.content == expected_content_by_name[entry["filename"]]


def test_temp_upload_batch_with_one_bad_extension_returns_400_and_saves_nothing(client):
    # File thứ 2 (giữa 2 file hợp lệ) có extension không hỗ trợ — all-or-nothing: toàn bộ
    # batch bị reject, KHÔNG file nào (kể cả a.pdf/c.pdf hợp lệ) được lưu xuống disk.
    resp = client.post(
        "/api/temp-upload",
        files=[
            ("files", ("a.pdf", b"content-a", "application/pdf")),
            ("files", ("virus.exe", b"content-bad", "application/octet-stream")),
            ("files", ("c.pdf", b"content-c", "application/pdf")),
        ],
    )

    assert resp.status_code == 400
    assert ".exe" in resp.json()["detail"]
    # Không leak file đã lưu trước khi validate hết batch — _entries phải rỗng.
    assert temp_files._entries == {}


# ---------------------------------------------------------------------------
# Regression — python-reviewer finding 🟠: OSError lúc SAVE (không phải lỗi extension bị
# chặn ở vòng validate TRƯỚC KHI lưu gì) file thứ N giữa batch phải rollback toàn bộ file
# batch này đã lưu trước đó qua temp_files.discard_temp_file(), không leak "thành công 1
# nửa" khi response cuối cùng báo lỗi cả batch. Khác test all-or-nothing extension ở trên
# (lỗi đó bị chặn TRƯỚC khi save() được gọi lần nào) — test này đi đúng qua nhánh
# try/except rollback mới quanh vòng save.
#
# Dùng TestClient riêng với raise_server_exceptions=False: TestClient mặc định (fixture
# `client` ở conftest.py) re-raise exception unhandled NGAY trong process test (không trả
# response) — verify thực nghiệm. Muốn thấy đúng response 500 mà 1 client thật/uvicorn sẽ
# nhận, phải tắt raise_server_exceptions.
# ---------------------------------------------------------------------------


def test_temp_upload_batch_save_oserror_midway_rolls_back_already_saved_files(monkeypatch):
    test_client = TestClient(main_module.app, raise_server_exceptions=False)
    test_client.cookies.set(auth.SESSION_COOKIE_NAME, auth.create_session_token("test-user"))

    original_save = temp_files.save_temp_file
    call_log: list[str] = []

    def _flaky_save(filename, content):
        call_log.append(filename)
        if filename == "b.pdf":
            raise OSError("disk full (simulated)")
        return original_save(filename, content)

    monkeypatch.setattr(temp_files, "save_temp_file", _flaky_save)

    resp = test_client.post(
        "/api/temp-upload",
        files=[
            ("files", ("a.pdf", b"content-a", "application/pdf")),
            ("files", ("b.pdf", b"content-b", "application/pdf")),
            ("files", ("c.pdf", b"content-c", "application/pdf")),
        ],
    )

    assert resp.status_code == 500
    # file thứ 3 KHÔNG được gọi tới — vòng save dừng ngay khi file thứ 2 raise OSError.
    assert call_log == ["a.pdf", "b.pdf"]
    # file thứ 1 đã lưu thành công trước đó phải bị rollback — không leak trong _entries.
    assert temp_files._entries == {}


def test_temp_upload_exactly_max_files_per_request_returns_200(client):
    cap = main_module.MAX_RELAY_FILES_PER_REQUEST
    resp = client.post(
        "/api/temp-upload",
        files=[("files", (f"f{i}.pdf", b"x", "application/pdf")) for i in range(cap)],
    )

    assert resp.status_code == 200
    assert len(resp.json()["files"]) == cap


def test_temp_upload_over_max_files_per_request_returns_400(client):
    cap = main_module.MAX_RELAY_FILES_PER_REQUEST
    resp = client.post(
        "/api/temp-upload",
        files=[("files", (f"f{i}.pdf", b"x", "application/pdf")) for i in range(cap + 1)],
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert str(cap) in detail
    assert str(cap + 1) in detail
    # Vượt cap bị chặn TRƯỚC khi lưu file nào — _entries phải rỗng.
    assert temp_files._entries == {}


# ---------------------------------------------------------------------------
# POST /api/temp-upload — guard "Thiếu file."/"Thiếu tên file." ở tầng function.
#
# Verify Empirically: cả field `files` bị thiếu HOÀN TOÀN (không multipart part nào tên
# "files") LẪN 1 part với filename rỗng (browser coi filename="" là KHÔNG phải file part,
# Starlette parse thành str thường) đều bị FastAPI/pydantic chặn ở 422 TRƯỚC KHI vào tới
# handler — verify thực nghiệm same behavior ở CẢ bản cũ (1 file, "file: UploadFile" bắt
# buộc) lẫn bản mới ("files: list[UploadFile]" bắt buộc), KHÔNG phải regression của diff
# này. `if not files: raise 400 "Thiếu file."` và `if not f.filename: raise 400 "Thiếu tên
# file."` trong main.py là guard PHÒNG XA không thể chạm được qua HTTP request thật với
# signature hiện tại — 2 test dưới gọi trực tiếp hàm `temp_upload()` (bỏ qua tầng
# HTTP/pydantic) để verify chính guard logic đó hoạt động đúng.
# ---------------------------------------------------------------------------


async def test_temp_upload_function_with_empty_files_list_raises_400_thieu_file():
    with pytest.raises(HTTPException) as exc_info:
        await main_module.temp_upload(request=None, files=[])

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Thiếu file."


async def test_temp_upload_function_with_blank_filename_in_batch_raises_400_thieu_ten_file():
    valid_file = UploadFile(file=io.BytesIO(b"content"), filename="a.pdf")
    blank_name_file = UploadFile(file=io.BytesIO(b"content"), filename="")

    with pytest.raises(HTTPException) as exc_info:
        await main_module.temp_upload(request=None, files=[valid_file, blank_name_file])

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Thiếu tên file."


# ---------------------------------------------------------------------------
# POST /api/temp-upload — validation
# ---------------------------------------------------------------------------


def test_temp_upload_with_unsupported_extension_returns_400(client):
    # .exe không thuộc bất kỳ engine WeKnora nào (anydoc/builtin/simple) — vẫn phải reject.
    # (KHÔNG dùng .txt nữa — .txt giờ hợp lệ, thuộc simple engine của ALLOWED_RELAY_EXTENSIONS.)
    resp = client.post(
        "/api/temp-upload",
        files=[("files", ("virus.exe", b"hello", "application/octet-stream"))],
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert ".pdf" in detail
    assert ".exe" in detail


def test_temp_upload_over_size_limit_returns_413(client, monkeypatch):
    monkeypatch.setattr(main_module, "MAX_RELAY_UPLOAD_BYTES", 10)
    resp = client.post(
        "/api/temp-upload",
        files=[("files", ("big.pdf", b"x" * 100, "application/pdf"))],
    )

    assert resp.status_code == 413


def test_temp_upload_at_exact_size_limit_still_succeeds(client, monkeypatch):
    monkeypatch.setattr(main_module, "MAX_RELAY_UPLOAD_BYTES", 10)
    resp = client.post(
        "/api/temp-upload",
        files=[("files", ("ok.pdf", b"x" * 10, "application/pdf"))],
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
# Regression — mở rộng ALLOWED_RELAY_EXTENSIONS cho .html (2026-09-20): lý do .html được
# coi an toàn để relay là FileResponse LUÔN trả Content-Disposition: attachment (không phải
# inline) — browser tải xuống thay vì render inline same-origin, nên không có stored-XSS.
# Test này khoá đúng behavior đó, không chỉ tin giả định.
# ---------------------------------------------------------------------------


def test_download_temp_html_file_has_attachment_content_disposition(unauthed_client):
    token, filename = temp_files.save_temp_file("page.html", b"<script>alert(1)</script>")

    resp = unauthed_client.get(f"/dl/{token}/{filename}")

    assert resp.status_code == 200
    assert resp.content == b"<script>alert(1)</script>"
    content_disposition = resp.headers.get("content-disposition", "")
    assert content_disposition.startswith("attachment")
    assert "inline" not in content_disposition


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
        files=[("files", ("report.pdf", b"content", "application/pdf"))],
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
        files=[("files", ("big.pdf", content, "application/pdf"))],
    )

    assert resp.status_code == 200
    url = resp.json()["files"][0]["url"]
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
            files=[("files", ("report.pdf", b"content", "application/pdf"))],
        )

    assert resp.status_code == 200
    assert any("PUBLIC_BASE_URL" in record.message for record in caplog.records)


def test_temp_upload_with_public_base_url_does_not_log_warning(client, monkeypatch, caplog):
    monkeypatch.setattr(main_module, "PUBLIC_BASE_URL", "https://relay.example.com")

    with caplog.at_level(logging.WARNING, logger="main"):
        resp = client.post(
            "/api/temp-upload",
            files=[("files", ("report.pdf", b"content", "application/pdf"))],
        )

    assert resp.status_code == 200
    assert not any("PUBLIC_BASE_URL" in record.message for record in caplog.records)
