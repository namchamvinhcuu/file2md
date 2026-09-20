import asyncio
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TEMP_UPLOAD_DIR = Path(os.environ.get("TEMP_UPLOAD_DIR", "/tmp/file2md-relay"))
TEMP_UPLOAD_TTL_SECONDS = int(os.environ.get("TEMP_UPLOAD_TTL_SECONDS", str(30 * 60)))
ALLOWED_RELAY_EXTENSIONS = {".pdf"}

_SWEEP_INTERVAL_SECONDS = 5 * 60
_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_STEM_LENGTH = 100


class UnsupportedRelayExtension(ValueError):
    pass


@dataclass
class _Entry:
    path: Path
    expires_at: float


_entries: dict[str, _Entry] = {}


def _safe_filename(original_name: str, ext: str) -> str:
    stem = Path(original_name).stem
    safe_stem = _SAFE_STEM_RE.sub("_", stem).strip("._") or "file"
    return f"{safe_stem[:_MAX_STEM_LENGTH]}{ext}"


def save_temp_file(original_filename: str, content: bytes) -> tuple[str, str]:
    """Lưu file relay tạm (blocking I/O — caller nên chạy qua asyncio.to_thread trong
    context async). Trả về (token, filename an toàn để đưa vào URL /dl/)."""
    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_RELAY_EXTENSIONS:
        raise UnsupportedRelayExtension(ext)
    token = secrets.token_urlsafe(24)
    filename = _safe_filename(original_filename, ext)
    token_dir = TEMP_UPLOAD_DIR / token
    token_dir.mkdir(parents=True, exist_ok=True)
    file_path = token_dir / filename
    try:
        file_path.write_bytes(content)
    except OSError:
        # Dọn dir vừa tạo để không leak vĩnh viễn — token này KHÔNG được đưa vào _entries
        # nên sweep_expired_loop() sẽ không bao giờ thấy nó để tự dọn.
        file_path.unlink(missing_ok=True)
        try:
            token_dir.rmdir()
        except OSError:
            logger.warning("Không dọn được token_dir mồ côi sau lỗi ghi: %s", token_dir)
        raise
    _entries[token] = _Entry(path=file_path, expires_at=time.monotonic() + TEMP_UPLOAD_TTL_SECONDS)
    return token, filename


def get_temp_file(token: str, filename: str) -> Optional[Path]:
    entry = _entries.get(token)
    if entry is None or entry.path.name != filename:
        return None
    if time.monotonic() > entry.expires_at:
        _discard(token)
        return None
    return entry.path


def _discard(token: str) -> None:
    entry = _entries.pop(token, None)
    if entry is None:
        return
    try:
        entry.path.unlink(missing_ok=True)
        entry.path.parent.rmdir()
    except OSError:
        logger.warning("Không xoá được temp-relay file token=%s", token)


async def sweep_expired_loop() -> None:
    while True:
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
        now = time.monotonic()
        expired = [token for token, entry in _entries.items() if now > entry.expires_at]
        for token in expired:
            await asyncio.to_thread(_discard, token)
