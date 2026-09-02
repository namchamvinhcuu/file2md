import io
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from markitdown import MarkItDown, StreamInfo

app = FastAPI(title="file2md")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ALLOWED_EXTENSIONS = {".txt", ".pdf"}

_markitdown = MarkItDown()


@app.post("/api/convert")
async def convert(text: str = Form(default=""), file: Optional[UploadFile] = None):
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
        markdown = text
        base_name = "converted"
    else:
        raise HTTPException(400, "Cần dán text hoặc chọn file .txt/.pdf")

    return JSONResponse({"markdown": markdown, "filename": f"{base_name}.md"})


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
