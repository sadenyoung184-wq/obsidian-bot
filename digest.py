"""گزارش صبحگاهی و هفتگی: خلاصه تسک‌ها، یادآورها و ترکر — بدون صدا زدن Gemini.

عمداً بدون AI است تا هر روز هزینه و زمان مصرف نکند؛ فقط از داده‌های محلی
(تودو، یادآورهای زمان‌بندی‌شده، ترکر دیروز) گزارش می‌سازد.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from config import settings

log = logging.getLogger(__name__)


def morning_digest(vault, reminders, chat_id: int) -> str:
    """گزارش صبح: تسک‌های باز + یادآورهای امروز + خلاصه دیروز."""
    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz)
    lines = [f"🌅 صبح بخیر! خلاصه امروز ({today.strftime('%Y-%m-%d')}):\n"]

    todos = vault.list_open_todos(limit=10)
    if todos:
        lines.append("📋 کارهای باز:")
        lines += [f"{i}. {t[6:][:120]}" for i, t in enumerate(todos, 1)]
    else:
        lines.append("📋 کاری نداری ✨")

    upcoming = [r for r in reminders.list_upcoming(chat_id)
                if r["next"][:10] <= today.strftime("%Y-%m-%d")]
    if upcoming:
        lines.append("\n⏰ یادآورهای امروز:")
        lines += [f"{r['repeat']} {r['text'][:100]} — {r['next'][11:16]}" for r in upcoming[:10]]
    else:
        lines.append("\n⏰ یادآوری برای امروز نداری.")

    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    ypath = vault.root / "Tracker" / f"{yesterday}.md"
    if ypath.exists():
        try:
            entries = [l for l in ypath.read_text(encoding="utf-8").splitlines()
                       if l.strip().startswith("- ")]
            lines.append(f"\n📊 دیروز {len(entries)} فعالیت ثبت کردی.")
        except OSError:
            pass

    lines.append("\nموفق باشی! 💪")
    return "\n".join(lines)


def weekly_digest(vault, reminders, chat_id: int) -> str:
    """گزارش هفتگی: آمار ۷ روز + تسک‌ها + یادآورهای فعال."""
    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz)
    lines = ["📈 گزارش هفته:\n"]
    total = 0
    for i in range(7):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        p = vault.root / "Tracker" / f"{day}.md"
        n = 0
        if p.exists():
            try:
                n = sum(1 for l in p.read_text(encoding="utf-8").splitlines()
                        if l.strip().startswith("- "))
            except OSError:
                pass
        total += n
        lines.append(f"{'امروز' if i == 0 else 'دیروز' if i == 1 else day}: {n} فعالیت")
    lines.append(f"\nجمع هفته: {total} فعالیت.")

    open_todos = vault.list_open_todos(limit=50)
    lines.append(f"📋 تسک‌های باز: {len(open_todos)}")
    upcoming = reminders.list_upcoming(chat_id)
    lines.append(f"⏰ یادآورهای فعال: {len(upcoming)}")
    return "\n".join(lines)
