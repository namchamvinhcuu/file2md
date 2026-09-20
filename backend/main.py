import asyncio
import io
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from markitdown import MarkItDown, StreamInfo
from starlette.middleware.base import BaseHTTPMiddleware

import auth
import db
import temp_files
from llm_providers import PROVIDERS, LLMReformatError, reformat_to_markdown

load_dotenv()

logger = logging.getLogger(__name__)

PUBLIC_PATHS = {"/api/login", "/login.html"}
# /dl/<token>/<filename> phải public — hệ thống ngoài (không có cookie) tự GET để tải file
# relay tạm (xem backend/temp_files.py). Bảo vệ bằng token ngẫu nhiên + TTL, không bằng login.
PUBLIC_PREFIXES = ("/dl/",)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)
        token = request.cookies.get(auth.SESSION_COOKIE_NAME)
        username = auth.verify_session_token(token) if token else None
        if not username:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Chưa đăng nhập."}, status_code=401)
            return RedirectResponse("/login.html")
        request.state.username = username
        response = await call_next(request)
        # KHÔNG cho browser cache nội dung sau đăng nhập — thiếu header này, browser tự cache
        # theo Last-Modified heuristic và có thể phục vụ lại trang cũ SAU KHI đã logout mà
        # không gọi lại server (verify thực nghiệm bằng Playwright: page.goto("/") ngay sau
        # logout vẫn trả 200 dù cookie đã bị xoá đúng trong browser).
        response.headers["Cache-Control"] = "no-store"
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await db.init_pool()
    sweep_task = asyncio.create_task(temp_files.sweep_expired_loop())
    try:
        yield
    finally:
        sweep_task.cancel()
        await db.close_pool()


app = FastAPI(title="file2md", lifespan=lifespan)
app.add_middleware(AuthMiddleware)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ALLOWED_EXTENSIONS = {".txt", ".pdf"}
LLM_MAX_TEXT_LENGTH = int(os.environ.get("LLM_MAX_TEXT_LENGTH", "20000"))
# URL public dùng để build link /dl/... trả về cho caller (VD OpenClaw). Rỗng → tự suy ra
# từ Host header của request (đủ dùng khi reverse-proxy forward Host gốc, như NPM đang làm).
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
MAX_RELAY_UPLOAD_BYTES = int(os.environ.get("MAX_RELAY_UPLOAD_BYTES", str(50 * 1024 * 1024)))
MAX_RELAY_FILES_PER_REQUEST = int(os.environ.get("MAX_RELAY_FILES_PER_REQUEST", "20"))

_markitdown = MarkItDown()

# Hash cố định dùng khi username không tồn tại — luôn gọi verify_password() (thay vì
# short-circuit) để thời gian phản hồi không tiết lộ user có tồn tại hay không.
_DUMMY_PASSWORD_HASH = auth.hash_password("dummy-password-for-constant-time-login")


@app.post("/api/login")
async def login(username: str = Form(...), password: str = Form(...)):
    password_hash = await db.get_password_hash(username)
    password_ok = auth.verify_password(password, password_hash or _DUMMY_PASSWORD_HASH)
    if not password_hash or not password_ok:
        raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu.")
    token = auth.create_session_token(username)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        auth.SESSION_COOKIE_NAME,
        token,
        max_age=auth.SESSION_MAX_AGE,
        httponly=True,
        secure=auth.COOKIE_SECURE,
        samesite="lax",
    )
    return response


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.SESSION_COOKIE_NAME)
    return response


@app.post("/api/convert")
async def convert(
    text: str = Form(default=""),
    provider: str = Form(default="none"),
    file: Optional[UploadFile] = None,
):
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                400, f"Chỉ hỗ trợ file .txt hoặc .pdf, nhận được: {ext or '(không rõ định dạng)'}"
            )
        raw = await file.read()
        if ext == ".txt":
            markdown = raw.decode("utf-8", errors="replace")
        else:
            result = _markitdown.convert_stream(
                io.BytesIO(raw), stream_info=StreamInfo(extension=".pdf")
            )
            markdown = result.markdown
        base_name = Path(file.filename).stem or "converted"
    elif text.strip():
        if provider != "none":
            if provider not in PROVIDERS:
                raise HTTPException(400, f"Provider không hỗ trợ: {provider}")
            if len(text) > LLM_MAX_TEXT_LENGTH:
                raise HTTPException(
                    413,
                    f"Text quá dài để reformat bằng AI (tối đa {LLM_MAX_TEXT_LENGTH} ký tự, "
                    f"nhận {len(text)}).",
                )
            try:
                markdown = await reformat_to_markdown(text, provider)
            except LLMReformatError:
                logger.exception("LLM reformat thất bại (provider=%s)", provider)
                raise HTTPException(
                    502, "Lỗi khi gọi AI để reformat văn bản. Vui lòng thử lại sau."
                ) from None
        else:
            markdown = text
        base_name = "converted"
    else:
        raise HTTPException(400, "Cần dán text hoặc chọn file .txt/.pdf")

    return JSONResponse({"markdown": markdown, "filename": f"{base_name}.md"})


_UPLOAD_CHUNK_SIZE = 1024 * 1024


async def _read_upload_within_limit(file: UploadFile, limit: int) -> bytes:
    # Đọc theo chunk + abort ngay khi vượt giới hạn — đọc hết `await file.read()` rồi mới
    # check len() sẽ buffer toàn bộ body (có thể nhiều GB) vào RAM trước khi bị reject.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, f"File vượt giới hạn {limit // (1024 * 1024)}MB.")
        chunks.append(chunk)
    return b"".join(chunks)


def _build_download_url(request: Request, token: str, filename: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/dl/{token}/{filename}"
    logger.warning(
        "PUBLIC_BASE_URL chưa set — URL /dl/ dùng scheme suy ra từ request, có thể sai "
        "http/https khi chạy sau reverse-proxy không forward đúng X-Forwarded-Proto."
    )
    return str(request.url_for("download_temp_file", token=token, filename=filename))


@app.post("/api/temp-upload")
async def temp_upload(request: Request, files: list[UploadFile]):
    if not files:
        raise HTTPException(400, "Thiếu file.")
    if len(files) > MAX_RELAY_FILES_PER_REQUEST:
        raise HTTPException(
            400, f"Tối đa {MAX_RELAY_FILES_PER_REQUEST} file mỗi lần upload, nhận {len(files)}."
        )

    # Validate extension của TẤT CẢ file trước khi lưu file nào — tránh rollback cho lỗi
    # extension (rẻ, check trước khi ghi gì cả). Lỗi size-limit (413) hay OSError lúc ghi
    # disk GIỮA vòng lưu vẫn cần rollback thật — xem try/except quanh vòng save dưới.
    for f in files:
        if not f.filename:
            raise HTTPException(400, "Thiếu tên file.")
        try:
            temp_files.check_extension(f.filename)
        except temp_files.UnsupportedRelayExtension as exc:
            ext = str(exc) or "(không rõ định dạng)"
            allowed = ", ".join(sorted(temp_files.ALLOWED_RELAY_EXTENSIONS))
            raise HTTPException(
                400,
                f"'{f.filename}': chỉ hỗ trợ relay file {allowed}, nhận được: {ext}",
            ) from None

    results = []
    saved_tokens: list[str] = []
    try:
        for f in files:
            content = await _read_upload_within_limit(f, MAX_RELAY_UPLOAD_BYTES)
            token, filename = await asyncio.to_thread(temp_files.save_temp_file, f.filename, content)
            saved_tokens.append(token)
            results.append({"filename": filename, "url": _build_download_url(request, token, filename)})
    except Exception:
        # Giữ đúng nghĩa all-or-nothing: 1 file giữa batch lỗi (413 vượt size, hoặc OSError
        # lúc ghi disk) → dọn sạch các file batch này đã lưu trước đó, đừng để leak "thành
        # công 1 nửa" mà response lại báo lỗi toàn batch.
        for saved_token in saved_tokens:
            await asyncio.to_thread(temp_files.discard_temp_file, saved_token)
        raise

    return JSONResponse({"files": results, "expires_in_seconds": temp_files.TEMP_UPLOAD_TTL_SECONDS})


@app.get("/dl/{token}/{filename}")
async def download_temp_file(token: str, filename: str):
    path = await asyncio.to_thread(temp_files.get_temp_file, token, filename)
    if path is None or not path.is_file():
        raise HTTPException(404, "File không tồn tại hoặc đã hết hạn.")
    response = FileResponse(path, filename=filename)
    response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
