import io
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from markitdown import MarkItDown, StreamInfo
from starlette.middleware.base import BaseHTTPMiddleware

import auth
import db
from llm_providers import PROVIDERS, LLMReformatError, reformat_to_markdown

load_dotenv()

logger = logging.getLogger(__name__)

PUBLIC_PATHS = {"/api/login", "/login.html"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in PUBLIC_PATHS:
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
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(title="file2md", lifespan=lifespan)
app.add_middleware(AuthMiddleware)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ALLOWED_EXTENSIONS = {".txt", ".pdf"}
LLM_MAX_TEXT_LENGTH = int(os.environ.get("LLM_MAX_TEXT_LENGTH", "20000"))

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


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
