"""استخراج متن از PDF و Word — بدون نیاز به اینترنت، سبک و سریع."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

MAX_CHARS = 12_000  # سقف متن ارسالی به Gemini


def extract_text(path: Path, max_chars: int = MAX_CHARS) -> str:
    """متن فایل را استخراج کن. خروجی خالی یعنی قابل استخراج نبود."""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return _from_pdf(path, max_chars)
        if ext in (".docx",):
            return _from_docx(path, max_chars)
        if ext in (".txt", ".md", ".csv"):
            return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception as exc:
        log.warning("extract failed %s: %s", path, exc)
    return ""


def _from_pdf(path: Path, max_chars: int) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages[:30]:  # سقف ۳۰ صفحه
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
        if sum(len(p) for p in parts) >= max_chars:
            break
    return "\n".join(parts)[:max_chars].strip()


def _from_docx(path: Path, max_chars: int) -> str:
    from docx import Document
    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return text[:max_chars].strip()
