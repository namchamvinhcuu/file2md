"""Unit test cho backend/temp_files.py — module thuần (không phụ thuộc FastAPI), quản lý
lưu/tra/xoá file relay tạm public (/dl/<token>/<filename>), TTL, sanitize filename, validate
extension. Storage đã cách ly khỏi TEMP_UPLOAD_DIR thật qua fixture autouse
`_isolated_relay_storage` (conftest.py) — mỗi test dùng tmp_path riêng.
"""

import asyncio
import contextlib
from pathlib import Path

import pytest

import temp_files


# ---------------------------------------------------------------------------
# save_temp_file — extension hợp lệ
# ---------------------------------------------------------------------------


def test_save_temp_file_valid_pdf_creates_file_on_disk(tmp_path):
    token, filename = temp_files.save_temp_file("report.pdf", b"%PDF-1.4 fake content")

    assert filename == "report.pdf"
    saved_path = tmp_path / token / filename
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"%PDF-1.4 fake content"


def test_save_temp_file_returns_token_registered_in_entries(tmp_path):
    token, filename = temp_files.save_temp_file("doc.pdf", b"data")

    assert token in temp_files._entries
    assert temp_files._entries[token].path == tmp_path / token / filename


# ---------------------------------------------------------------------------
# save_temp_file — extension KHÔNG hợp lệ
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    # .txt KHÔNG còn dùng ở đây — .txt giờ hợp lệ (simple engine trong ALLOWED_RELAY_EXTENSIONS).
    "bad_name",
    ["malware.bin", "malware.exe", "no-extension-at-all"],
)
def test_save_temp_file_unsupported_extension_raises(bad_name):
    with pytest.raises(temp_files.UnsupportedRelayExtension):
        temp_files.save_temp_file(bad_name, b"content")


def test_save_temp_file_unsupported_extension_does_not_write_any_file(tmp_path):
    with contextlib.suppress(temp_files.UnsupportedRelayExtension):
        temp_files.save_temp_file("malware.bin", b"content")

    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# save_temp_file — ALLOWED_RELAY_EXTENSIONS mở rộng (2026-09-20, WeKnora cần relay nhiều
# loại tài liệu ngoài PDF) — mỗi extension mới phải được accept đúng, ghi file đúng nội dung.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "page.html",
        "page.htm",
        "notes.md",
        "image.png",
        "photo.jpg",
        "photo.jpeg",
        "doc.docx",
        "slides.pptx",
        "sheet.xlsx",
    ],
)
def test_save_temp_file_accepts_each_extended_extension(filename, tmp_path):
    token, saved_filename = temp_files.save_temp_file(filename, b"content")

    assert saved_filename == filename
    saved_path = tmp_path / token / saved_filename
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"content"


# ---------------------------------------------------------------------------
# save_temp_file — mở rộng ALLOWED_RELAY_EXTENSIONS lần 2 (2026-09-20, 36 extension gộp 3
# engine WeKnora: anydoc/builtin/simple) — test ĐẠI DIỆN cho mỗi engine, không test hết 36.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        # anydoc engine — thêm mới ngoài .docx/.pptx/.xlsx đã test ở trên
        "legacy.doc",
        "book.epub",
        "letter.rtf",
        "mindmap.xmind",
        # builtin engine — thêm mới ngoài .html/.htm/.md/.png/.jpg/.jpeg đã test ở trên
        "archive.mhtml",
        "readme.markdown",
        "photo.gif",
        "photo.webp",
        # simple engine — HOÀN TOÀN MỚI (trước lần mở rộng này, .txt/.csv/.json bị reject)
        "data.csv",
        "notes.txt",
        "config.json",
        "song.mp3",
        "voice.wav",
    ],
)
def test_save_temp_file_accepts_representative_extension_per_weknora_engine(filename, tmp_path):
    token, saved_filename = temp_files.save_temp_file(filename, b"content")

    assert saved_filename == filename
    saved_path = tmp_path / token / saved_filename
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"content"


# ---------------------------------------------------------------------------
# Regression — mở rộng ALLOWED_RELAY_EXTENSIONS (2026-09-20, giờ 36 extension gộp 3 engine
# WeKnora: anydoc/builtin/simple) KHÔNG được vô tình accept-all: extension ngoài TOÀN BỘ 3
# engine vẫn phải reject. (.txt/.csv/.json/.md/.html/... giờ đã hợp lệ nên KHÔNG dùng ở đây
# nữa — dùng loại binary/executable không thuộc bất kỳ engine nào.)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    ["malware.exe", "archive.zip", "trojan.dll", "no-extension-at-all"],
)
def test_save_temp_file_still_rejects_extensions_outside_extended_allowlist(bad_name):
    with pytest.raises(temp_files.UnsupportedRelayExtension):
        temp_files.save_temp_file(bad_name, b"content")


# ---------------------------------------------------------------------------
# save_temp_file — case-sensitivity: extension viết hoa vẫn được nhận, vì save_temp_file()
# dùng Path(original_filename).suffix.lower() để validate/tạo tên file cuối.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["REPORT.PDF", "Slide.PPTX", "Image.PNG"])
def test_save_temp_file_accepts_uppercase_extension(filename, tmp_path):
    token, saved_filename = temp_files.save_temp_file(filename, b"content")

    expected_filename = Path(filename).stem + Path(filename).suffix.lower()
    assert saved_filename == expected_filename
    saved_path = tmp_path / token / saved_filename
    assert saved_path.exists()


# ---------------------------------------------------------------------------
# get_temp_file — token/filename đúng vs sai
# ---------------------------------------------------------------------------


def test_get_temp_file_correct_token_and_filename_returns_existing_path(tmp_path):
    token, filename = temp_files.save_temp_file("report.pdf", b"data")

    result = temp_files.get_temp_file(token, filename)

    assert result == tmp_path / token / filename
    assert result.exists()


def test_get_temp_file_wrong_token_returns_none():
    temp_files.save_temp_file("report.pdf", b"data")

    assert temp_files.get_temp_file("token-does-not-exist", "report.pdf") is None


def test_get_temp_file_wrong_filename_same_token_returns_none():
    token, _filename = temp_files.save_temp_file("report.pdf", b"data")

    assert temp_files.get_temp_file(token, "other-name.pdf") is None


# ---------------------------------------------------------------------------
# get_temp_file — TTL hết hạn
# ---------------------------------------------------------------------------


def test_get_temp_file_after_ttl_expired_returns_none_and_deletes_file(monkeypatch, tmp_path):
    monkeypatch.setattr(temp_files, "TEMP_UPLOAD_TTL_SECONDS", -1)  # expires_at đã ở quá khứ
    token, filename = temp_files.save_temp_file("report.pdf", b"data")
    file_path = tmp_path / token / filename
    assert file_path.exists()

    result = temp_files.get_temp_file(token, filename)

    assert result is None
    assert not file_path.exists()
    assert token not in temp_files._entries


def test_get_temp_file_before_ttl_expires_still_returns_path(monkeypatch, tmp_path):
    monkeypatch.setattr(temp_files, "TEMP_UPLOAD_TTL_SECONDS", 30 * 60)
    token, filename = temp_files.save_temp_file("report.pdf", b"data")

    result = temp_files.get_temp_file(token, filename)

    assert result is not None
    assert result.exists()


# ---------------------------------------------------------------------------
# _safe_filename / save_temp_file — path traversal + ký tự đặc biệt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malicious_name",
    [
        "../../etc/passwd.pdf",
        "..\\..\\x.pdf",
        "evil/../../secrets.pdf",
    ],
)
def test_save_temp_file_path_traversal_filename_stays_within_token_dir(malicious_name, tmp_path):
    token, filename = temp_files.save_temp_file(malicious_name, b"content")

    assert "/" not in filename
    assert ".." not in filename
    saved_path = temp_files._entries[token].path
    assert saved_path.parent == tmp_path / token
    assert saved_path.is_relative_to(tmp_path)
    assert saved_path.exists()


def test_save_temp_file_unicode_vietnamese_filename_is_sanitized_to_ascii(tmp_path):
    token, filename = temp_files.save_temp_file("Báo cáo.pdf", b"content")

    assert filename.endswith(".pdf")
    assert filename.isascii()
    assert "/" not in filename
    saved_path = temp_files._entries[token].path
    assert saved_path.parent == tmp_path / token
    assert saved_path.exists()


def test_save_temp_file_filename_with_spaces_is_sanitized(tmp_path):
    token, filename = temp_files.save_temp_file("a b c.pdf", b"content")

    assert " " not in filename
    saved_path = temp_files._entries[token].path
    assert saved_path.exists()


def test_save_temp_file_null_byte_in_filename_is_sanitized(tmp_path):
    """Ký tự NUL không hợp lệ trong filesystem path thật — _SAFE_STEM_RE phải loại nó khỏi
    filename cuối cùng trước khi ghi ra disk, không để lọt raw byte NUL."""
    token, filename = temp_files.save_temp_file("evil\x00.pdf", b"content")

    assert "\x00" not in filename
    saved_path = temp_files._entries[token].path
    assert saved_path.exists()


def test_safe_filename_directly_never_produces_double_dot_or_slash():
    for original, ext in [
        ("../../etc/passwd.pdf", ".pdf"),
        ("..\\..\\x.pdf", ".pdf"),
        ("Báo cáo cuối năm.pdf", ".pdf"),
    ]:
        result = temp_files._safe_filename(original, ext)
        assert "/" not in result
        assert ".." not in result


# ---------------------------------------------------------------------------
# sweep_expired_loop — dọn rác entry hết hạn
# ---------------------------------------------------------------------------


async def test_sweep_expired_loop_removes_expired_entries(monkeypatch, tmp_path):
    """Chạy sweep_expired_loop() thật (không reimplement logic riêng) trong 1 asyncio Task,
    rút ngắn _SWEEP_INTERVAL_SECONDS về 0 để vòng lặp không phải chờ 5 phút, rồi cancel
    ngay sau khi thấy entry hết hạn đã bị dọn — verify đúng behavior của hàm thật."""
    monkeypatch.setattr(temp_files, "_SWEEP_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(temp_files, "TEMP_UPLOAD_TTL_SECONDS", -1)  # hết hạn ngay khi tạo
    token, filename = temp_files.save_temp_file("report.pdf", b"data")
    file_path = tmp_path / token / filename
    assert file_path.exists()

    task = asyncio.create_task(temp_files.sweep_expired_loop())
    try:
        for _ in range(200):
            await asyncio.sleep(0)
            if token not in temp_files._entries:
                break
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert token not in temp_files._entries
    assert not file_path.exists()


# ---------------------------------------------------------------------------
# Regression — python-reviewer finding 🟠 "orphan dir leak": trước fix, write_bytes() raise
# OSError giữa lúc mkdir() đã tạo token_dir → token_dir mồ côi vĩnh viễn trên disk (không có
# trong _entries nên sweep_expired_loop() không bao giờ thấy để dọn). Fix: bọc write_bytes
# trong try/except OSError, cleanup file+dir rồi re-raise.
# ---------------------------------------------------------------------------


def test_save_temp_file_write_failure_cleans_up_orphan_token_dir(monkeypatch, tmp_path):
    def _boom(self, data):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(Path, "write_bytes", _boom)

    with pytest.raises(OSError):
        temp_files.save_temp_file("report.pdf", b"content")

    # token_dir vừa mkdir() phải được dọn — KHÔNG để lại mồ côi trên disk.
    assert list(tmp_path.iterdir()) == []
    assert temp_files._entries == {}


# ---------------------------------------------------------------------------
# Regression — python-reviewer finding 🟠: filename gốc quá dài (vd bị người dùng/hệ thống
# ngoài đặt tên hàng trăm ký tự) có thể khiến path cuối cùng vượt ENAMETOOLONG khi ghi
# xuống filesystem thật. Fix: `_MAX_STEM_LENGTH = 100` cắt safe_stem trong _safe_filename().
# ---------------------------------------------------------------------------


def test_safe_filename_truncates_very_long_stem_to_max_length():
    long_name = "a" * 300 + ".pdf"

    result = temp_files._safe_filename(long_name, ".pdf")

    assert len(result) <= temp_files._MAX_STEM_LENGTH + len(".pdf")


def test_save_temp_file_with_very_long_filename_does_not_raise_and_stays_within_max_length(tmp_path):
    long_name = "b" * 300 + ".pdf"

    token, filename = temp_files.save_temp_file(long_name, b"content")

    assert len(filename) <= temp_files._MAX_STEM_LENGTH + len(".pdf")
    saved_path = tmp_path / token / filename
    assert saved_path.exists()


async def test_sweep_expired_loop_keeps_non_expired_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(temp_files, "_SWEEP_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(temp_files, "TEMP_UPLOAD_TTL_SECONDS", 30 * 60)  # còn hạn lâu
    token, filename = temp_files.save_temp_file("report.pdf", b"data")
    file_path = tmp_path / token / filename

    task = asyncio.create_task(temp_files.sweep_expired_loop())
    # để loop chạy vài vòng nhường CPU rồi cancel — entry chưa hết hạn phải còn nguyên
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert token in temp_files._entries
    assert file_path.exists()
