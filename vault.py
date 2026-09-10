"""مدیریت والت Obsidian: ساخت نوت، تودو، ترکر و لینک‌سازی به نوت‌های قدیمی."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo

from config import settings

log = logging.getLogger(__name__)

FOLDERS = ["Inbox", "Classes", "Events", "Reviews", "Tasks", "Reminders", "Ideas", "Journal", "Tracker"]

INVALID_CHARS = re.compile(r'[\\/:"*?<>|#^\[\]]')


def slugify(title: str, max_len: int = 60) -> str:
    clean = INVALID_CHARS.sub("", title).strip()
    clean = re.sub(r"\s+", "-", clean)
    return clean[:max_len] or "note"


class ObsidianVault:
    def __init__(self, root: str | None = None, tz: str | None = None):
        self.root = Path(root or settings.vault_path).expanduser()
        self.tz = ZoneInfo(tz or settings.timezone)
        self.ensure_structure()

    # ---------- ساختار ----------

    def ensure_structure(self) -> None:
        for folder in FOLDERS:
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    # ---------- ذخیره نوت ----------

    def save_note(self, result: dict) -> Path:
        """نتیجه تحلیل brain را به یک فایل مارک‌داون تبدیل و ذخیره می‌کند."""
        folder = result.get("folder") or "Inbox"
        title = (result.get("title") or "بدون عنوان").strip()
        date_str = self.now().strftime("%Y-%m-%d")
        time_str = self.now().strftime("%H:%M")

        filename = f"{date_str}-{slugify(title)}.md"
        path = self.root / folder / filename
        counter = 2
        while path.exists():
            path = self.root / folder / f"{date_str}-{slugify(title)}-{counter}.md"
            counter += 1

        related = self.find_related(
            keywords=result.get("keywords") or [],
            exclude=path,
            limit=5,
        )

        frontmatter = self._frontmatter(result, date_str, time_str)
        body = (result.get("body") or "").strip()
        related_section = self._related_section(related)
        content = f"{frontmatter}\n\n{body}\n{related_section}"
        path.write_text(content, encoding="utf-8")

        # لینک برگشتی در نوت‌های قدیمی تا گراف Obsidian دوطرفه شود
        self._add_backlinks(related, new_note_name=path.stem)

        # ثبت خودکار در ترکر و تودو
        if result.get("tracker"):
            self.log_tracker(f"[[{path.stem}]] — {title}")
        if result.get("type") in ("task", "reminder") or folder in ("Tasks", "Reminders"):
            self.add_todo(title, due=result.get("due"), source=path.stem)

        # اگر سینک گیت فعال است، به گیت‌هاب پوش کن (خطا ربات را نمی‌خواباند)
        try:
            from git_sync import sync_changes
            sync_changes(self.root, f"bot: {folder}/{path.name}")
        except Exception:  # pragma: no cover
            log.warning("git sync failed, continuing locally", exc_info=True)

        log.info("Note saved: %s", path)
        return path

    def _frontmatter(self, result: dict, date_str: str, time_str: str) -> str:
        tags = " ".join(f"#{t}" for t in (result.get("tags") or []))
        lines = ["---", f'title: "{result.get("title")}"', f"date: {date_str} {time_str}",
                 f'type: {result.get("type")}', f'folder: {result.get("folder")}',
                 f'tags: [{", ".join(result.get("tags") or [])}]']
        if result.get("due"):
            lines.append(f'due: {result.get("due")}')
        if result.get("remind_at"):
            lines.append(f'remind_at: {result.get("remind_at")}')
        lines.append("---")
        return "\n".join(lines)

    def _related_section(self, related: list[Path]) -> str:
        if not related:
            return ""
        links = "\n".join(f"- [[{p.stem}]]" for p in related)
        return f"\n\n## 🔗 نوت‌های مرتبط\n{links}\n"

    # ---------- لینک‌سازی ----------

    def find_related(self, keywords: list[str], exclude: Path | None = None, limit: int = 5) -> list[Path]:
        """نوت‌های قدیمی که کلمات کلیدی در آن‌ها آمده را با امتیاز ساده پیدا کن."""
        keywords = [k.strip() for k in keywords if k and len(k.strip()) >= 2]
        if not keywords:
            return []
        scored: list[tuple[int, Path]] = []
        for md in self.root.rglob("*.md"):
            if exclude and md.resolve() == exclude.resolve():
                continue
            if "Tracker" in md.parts:  # فایل‌های ترکر روزانه در لینک‌سازی دخالت نکنند
                continue
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            score = sum(text.count(kw) * (3 if f"[[{kw}]]" in text or kw in md.stem else 1)
                        for kw in keywords)
            if score > 0:
                scored.append((score, md))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:limit]]

    def _add_backlinks(self, related: list[Path], new_note_name: str) -> None:
        link_line = f"- [[{new_note_name}]]"
        for old in related:
            try:
                text = old.read_text(encoding="utf-8")
            except OSError:
                continue
            if new_note_name in text:
                continue
            if "## 🔗 نوت‌های مرتبط" in text:
                text = text.rstrip() + f"\n{link_line}\n"
            else:
                text = text.rstrip() + f"\n\n## 🔗 نوت‌های مرتبط\n{link_line}\n"
            try:
                old.write_text(text, encoding="utf-8")
            except OSError:
                log.warning("Could not update backlink in %s", old)

    # ---------- عملیات مدیریتی (type=manage) ----------

    def _safe_path(self, rel: str) -> Path | None:
        """مسیر نسبی را به مسیر امن داخل والت تبدیل کن؛ خروج از والت → None."""
        rel = (rel or "").strip()
        if not rel or rel.startswith(("/", "\\", ".", "~")):
            return None
        rel = rel.replace("\\", "/")
        if "/../" in f"/{rel}/" or rel in (".", ".."):
            return None
        try:
            p = (self.root / rel).resolve()
            p.relative_to(self.root.resolve())
        except (ValueError, OSError):
            return None
        return p

    def _after_change(self, message: str) -> None:
        """بعد از هر تغییر: ایندکس حافظه را تازه کن + به گیت پوش کن."""
        try:
            from memory import refresh_index
            refresh_index(self.root)
        except Exception:
            log.warning("memory refresh failed", exc_info=True)
        try:
            from git_sync import sync_changes
            sync_changes(self.root, message)
        except Exception:
            log.warning("git sync failed, continuing locally", exc_info=True)

    def read_note(self, rel_path: str, max_chars: int = 4000) -> str | None:
        p = self._safe_path(rel_path)
        if p is None or not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8")[:max_chars]
        except OSError:
            return None

    def write_note(self, rel_path: str, content: str) -> Path | None:
        """ساخت یا بازنویسی کامل نوت. فقط فایل md داخل والت."""
        if not rel_path.lower().endswith(".md"):
            rel_path = rel_path.rstrip("/") + ".md"
        p = self._safe_path(rel_path)
        if p is None:
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "", encoding="utf-8")
        self._after_change(f"bot: write {p.relative_to(self.root).as_posix()}")
        return p

    def append_note(self, rel_path: str, text: str) -> Path | None:
        p = self._safe_path(rel_path)
        if p is None or not p.is_file():
            return None
        with p.open("a", encoding="utf-8") as f:
            f.write("\n" + (text or "").strip() + "\n")
        self._after_change(f"bot: append {p.relative_to(self.root).as_posix()}")
        return p

    def rename_note(self, rel_path: str, new_name: str) -> Path | None:
        p = self._safe_path(rel_path)
        if p is None or not p.is_file():
            return None
        clean = INVALID_CHARS.sub("", (new_name or "").strip()).replace("/", "")
        if not clean:
            return None
        if not clean.lower().endswith(".md"):
            clean += ".md"
        dest = p.parent / clean
        if dest.exists():
            return None
        p.rename(dest)
        self._after_change(f"bot: rename {rel_path} -> {dest.name}")
        return dest

    def move_note(self, rel_path: str, dest_folder: str) -> Path | None:
        p = self._safe_path(rel_path)
        folder = (dest_folder or "").strip().strip("/").replace("\\", "/")
        if p is None or not p.is_file() or not folder or ".." in folder:
            return None
        dest_dir = self.root / folder
        try:
            dest_dir.resolve().relative_to(self.root.resolve())
        except (ValueError, OSError):
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / p.name
        if dest.exists():
            return None
        p.rename(dest)
        self._after_change(f"bot: move {rel_path} -> {folder}/")
        return dest

    def delete_note(self, rel_path: str) -> Path | None:
        """حذف امن: نوت به .trash/ منتقل می‌شود (قابل بازیابی)، پاک دائم نمی‌شود."""
        if rel_path.strip().rstrip("/") in ("Tasks/Todo.md", "Todo.md"):
            return None  # تودولیست اصلی را نمی‌شود حذف کرد
        p = self._safe_path(rel_path)
        if p is None or not p.is_file():
            return None
        trash = self.root / ".trash" / self.now().strftime("%Y-%m-%d")
        trash.mkdir(parents=True, exist_ok=True)
        dest = trash / p.name
        counter = 2
        while dest.exists():
            dest = trash / f"{p.stem}-{counter}{p.suffix}"
            counter += 1
        p.rename(dest)
        self._after_change(f"bot: trash {rel_path}")
        return dest

    def make_folder(self, folder: str) -> Path | None:
        folder = (folder or "").strip().strip("/").replace("\\", "/")
        if not folder or ".." in folder or folder.startswith("."):
            return None
        d = self.root / folder
        try:
            d.resolve().relative_to(self.root.resolve())
        except (ValueError, OSError):
            return None
        d.mkdir(parents=True, exist_ok=True)
        self._after_change(f"bot: mkdir {folder}")
        return d

    def remove_folder(self, folder: str) -> bool:
        """فقط پوشه خالی حذف می‌شود؛ غیرخالی → False."""
        folder = (folder or "").strip().strip("/")
        if not folder or folder in [f.name for f in self.root.iterdir() if False]:
            return False
        d = self._safe_path(folder)
        if d is None or not d.is_dir():
            return False
        try:
            d.rmdir()  # فقط اگر خالی باشد موفق می‌شود
        except OSError:
            return False
        self._after_change(f"bot: rmdir {folder}")
        return True

    def list_notes(self, folder: str | None = None, limit: int = 50) -> list[str]:
        base = self.root if not folder else self._safe_path(folder.strip().strip("/"))
        if base is None or not base.is_dir():
            return []
        out = []
        for md in sorted(base.rglob("*.md")):
            if ".trash" in md.parts or ".git" in md.parts:
                continue
            out.append(md.relative_to(self.root).as_posix())
            if len(out) >= limit:
                break
        return out

    # ---------- تودو ----------

    @property
    def todo_file(self) -> Path:
        return self.root / "Tasks" / "Todo.md"

    def add_todo(self, title: str, due: str | None = None, source: str | None = None) -> None:
        line = f"- [ ] {title}"
        if due:
            line += f" ⏳ {due[:16].replace('T', ' ')}"
        if source:
            line += f" 📎 [[{source}]]"
        if not self.todo_file.exists():
            self.todo_file.write_text("# ✅ تودولیست\n\n", encoding="utf-8")
        with self.todo_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def list_open_todos(self, limit: int = 20) -> list[str]:
        if not self.todo_file.exists():
            return []
        todos = []
        for line in self.todo_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("- [ ]"):
                todos.append(line.strip())
                if len(todos) >= limit:
                    break
        return todos

    def complete_todo(self, index: int) -> bool:
        """تیک زدن تودوی شماره index (شمارش از ۱، مطابق ترتیب فایل)."""
        if not self.todo_file.exists():
            return False
        lines = self.todo_file.read_text(encoding="utf-8").splitlines(keepends=True)
        open_idx = 0
        changed = False
        for i, line in enumerate(lines):
            if line.strip().startswith("- [ ]"):
                open_idx += 1
                if open_idx == index:
                    lines[i] = line.replace("- [ ]", "- [x]", 1)
                    changed = True
                    break
        if changed:
            self.todo_file.write_text("".join(lines), encoding="utf-8")
        return changed

    # ---------- ترکر روزانه ----------

    def log_tracker(self, text: str) -> Path:
        day = self.now().strftime("%Y-%m-%d")
        path = self.root / "Tracker" / f"{day}.md"
        if not path.exists():
            path.write_text(f"# 📊 ترکر {day}\n\n", encoding="utf-8")
        time_str = self.now().strftime("%H:%M")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- {time_str} — {text}\n")
        return path
