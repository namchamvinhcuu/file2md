# CLAUDE.md

Behavioral principles cho Claude khi code **file2md** — web app nhỏ: dán text hoặc upload file `.txt`/`.pdf` → convert sang Markdown, tải về `.md`. Stack: **Python (FastAPI) backend + HTML/CSS/JS thuần (không build step) cho frontend**, dùng lib `markitdown` để convert PDF.

**Knowledge base:**
- 📚 `./.obsidian-vault/` — Obsidian vault project (Architecture/Features/Fix-History/Change-Log). Vào qua `Index.md`. Symlink per-machine, trong `.gitignore`.

**Sub-agents:** `python-reviewer`, `python-test-writer`, `vault-debugger`, `vault-curator` — discipline chung `.claude/references/{review,test,sub-agent}-discipline.md`. **Priority khi conflict:** Project CLAUDE.md > global `~/.claude/CLAUDE.md` > references/ > agent body. Orchestration/auto-run/Test-Driven Quality → **theo global**, file này KHÔNG lặp lại.

### Per-machine setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn main:app --app-dir backend --port 8000   # http://127.0.0.1:8000/
```
Verify: `ls .obsidian-vault/Index.md .venv/bin/python`. Cả `.obsidian-vault` + `.venv` ở `.gitignore`.

---

## Project invariants (KHÔNG tự đổi khi chưa hỏi)

- **`markitdown[pdf]`** (`backend/requirements.txt`) là engine convert PDF duy nhất — KHÔNG tự viết PDF parser riêng (ladder Simplicity First: dependency đã cài > viết mới).
- File `.txt` / text paste: **KHÔNG** đi qua `markitdown` — pass-through trực tiếp (`backend/main.py`), vì đã là plain text sẵn, không cần transform.
- Chỉ hỗ trợ upload `.txt` / `.pdf` (`ALLOWED_EXTENSIONS` trong `backend/main.py`). `markitdown` hỗ trợ sẵn docx/pptx/xlsx nhưng **chưa wire** — muốn thêm định dạng thì hỏi trước.
- Frontend **cố tình không có build step** (`frontend/index.html` — 1 file HTML/CSS/JS thuần) — đừng tự thêm React/Vite/npm trừ khi được yêu cầu.
- Backend serve luôn frontend qua `StaticFiles` (`app.mount("/", ...)` cuối `backend/main.py`) → same-origin, không cần CORS. Tách frontend ra domain khác thì phải tính lại CORS.

## Trigger đặc thù project (BẮT BUỘC spawn, không lách)

- Đổi logic convert (`backend/main.py`, đặc biệt nhánh `.pdf`) → `python-test-writer` (cover cả file .txt lẫn .pdf, có test tiếng Việt/unicode).
- Diff đụng nhánh xử lý **file upload từ user** (đọc `UploadFile`, ghép path, đặt tên file tải về) → `python-reviewer` **bắt buộc** (thuộc lớp security "xử lý input từ ngoài" ở global, không phải tùy chọn).
