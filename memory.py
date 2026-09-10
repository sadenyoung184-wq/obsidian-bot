"""حافظه بلندمدت والت: ایندکس همه نوت‌ها که به پرامپت Gemini تزریق می‌شود.

ایده: ربات قبل از هر تصمیم، کل والت را «می‌بیند» — ساختار پوشه‌ها،
تیتر نوت‌ها و خلاصه محتوایشان — پس جواب‌هایش با دانش واقعی توست نه حدس.
ایندکس در memory.json کش می‌شود و فقط وقتی فایلی عوض شده باشد بازسازی می‌شود.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

INDEX_FILE = Path(__file__).parent / "memory.json"

# سقف‌ها تا پرامپت از حد توکن مدل بیرون نزند
MAX_NOTES = 400          # حداکثر نوت در ایندکس
MAX_CHARS_PER_NOTE = 800  # حداکثر کاراکتر خلاصه هر نوت
MAX_TOTAL_CHARS = 60_000  # سقف کل متن حافظه

SKIP_DIRS = {".git", ".obsidian", ".trash", "Assets"}


def _fingerprint(root: Path) -> str:
    """اثرانگشت سریع والت: مسیر + سایز + زمان تغییر همه mdها."""
    h = hashlib.md5()
    try:
        files = sorted(root.rglob("*.md"))
    except OSError:
        return ""
    for f in files:
        try:
            st = f.stat()
            h.update(f"{f.relative_to(root)}:{st.st_size}:{int(st.st_mtime)};".encode())
        except OSError:
            continue
    return h.hexdigest()


def build_index(root: Path) -> dict:
    """ایندکس تازه بساز: {fingerprint, notes: [{path, folder, title, summary}]}."""
    notes: list[dict] = []
    try:
        files = sorted(root.rglob("*.md"))
    except OSError:
        files = []
    for f in files:
        if any(part in SKIP_DIRS for part in f.parts):
            continue
        if f.name == "reminders.md":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = f.relative_to(root).as_posix()
        folder = f.parent.name
        title = f.stem
        for line in text.splitlines()[:15]:
            line = line.strip()
            if line.startswith("# "):
                title = line[2:].strip()
                break
        # حذف فرانت‌متر برای خلاصه تمیزتر
        body = text
        if body.startswith("---"):
            end = body.find("---", 3)
            if end != -1:
                body = body[end + 3:]
        summary = " ".join(body.split())[:MAX_CHARS_PER_NOTE]
        notes.append({"path": rel, "folder": folder, "title": title, "summary": summary})
        if len(notes) >= MAX_NOTES:
            break
    return {"fingerprint": _fingerprint(root), "notes": notes}


def get_index(root: Path) -> dict:
    """ایندکس کش‌شده را بده؛ اگر والت عوض شده بازسازی کن."""
    fp = _fingerprint(root)
    if INDEX_FILE.exists():
        try:
            cached = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fp and isinstance(cached.get("notes"), list):
                return cached
        except (json.JSONDecodeError, OSError):
            pass
    index = build_index(root)
    try:
        INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("Could not write memory index")
    return index


def refresh_index(root: Path) -> dict:
    """بازسازی اجباری ایندکس (بعد از هر ذخیره/حذف صدا بزن)."""
    index = build_index(root)
    try:
        INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return index


def memory_context(root: Path) -> str:
    """متن حافظه برای تزریق به پرامپت Gemini."""
    index = get_index(root)
    notes = index.get("notes", [])
    if not notes:
        return "والت هنوز نوتی ندارد."
    # گروه‌بندی بر اساس پوشه برای خوانایی
    by_folder: dict[str, list[dict]] = {}
    for n in notes:
        by_folder.setdefault(n["folder"], []).append(n)
    total = 0
    parts = [f"کل والت {len(notes)} نوت در {len(by_folder)} پوشه دارد. خلاصه نوت‌ها:"]
    for folder in sorted(by_folder):
        parts.append(f"\n[{folder}]")
        for n in by_folder[folder]:
            line = f"- {n['path']} | {n['title']} | {n['summary']}"
            if total + len(line) > MAX_TOTAL_CHARS:
                parts.append("... (بقیه به‌خاطر سقف طول حذف شد)")
                return "\n".join(parts)
            parts.append(line)
            total += len(line)
    return "\n".join(parts)
