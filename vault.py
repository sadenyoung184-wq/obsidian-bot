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
