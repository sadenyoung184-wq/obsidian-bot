"""سیستم یادآور: هم در تلگرام پیام می‌فرستد، هم در Obsidian می‌ماند.

یادآورها داخل فایل Reminders/reminders.md ذخیره می‌شوند تا با
ری‌استارت شدن ربات از بین نروند.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from zoneinfo import ZoneInfo

from config import settings

log = logging.getLogger(__name__)

STORE = Path(__file__).parent / "Reminders" / "reminders.md"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(settings.timezone))
        return dt
    except ValueError:
        return None


class ReminderService:
    def __init__(self, bot, vault):
        self.bot = bot
        self.vault = vault
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    # ---------- API ----------

    def schedule(self, chat_id: int, text: str, remind_at: str | None) -> bool:
        """یادآور جدید ثبت کن. اگر زمان معتبر نباشد False برمی‌گرداند."""
        dt = _parse_dt(remind_at)
        if dt is None or dt <= datetime.now(ZoneInfo(settings.timezone)):
            return False
        job_id = f"{chat_id}-{int(dt.timestamp())}-{abs(hash(text)) % 10_000}"
        self.scheduler.add_job(
            self._fire,
            "date",
            run_date=dt,
            id=job_id,
            replace_existing=True,
            kwargs={"chat_id": chat_id, "text": text},
        )
        self._persist(chat_id, text, dt)
        return True

    def start(self) -> None:
        self._restore()
        self.scheduler.start()
        log.info("Reminder scheduler started")

    # ---------- اجرا ----------

    async def _fire(self, chat_id: int, text: str) -> None:
        message = f"⏰ یادآوری:\n\n{text}"
        try:
            await self.bot.send_message(chat_id, message)
        except Exception as exc:
            log.warning("Could not send reminder to %s: %s", chat_id, exc)
        self.vault.log_tracker(f"⏰ یادآوری ارسال شد: {text}")

    # ---------- ماندگاری ----------

    def _persist(self, chat_id: int, text: str, dt: datetime) -> None:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        record = {"chat_id": chat_id, "text": text, "at": dt.isoformat()}
        with STORE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _restore(self) -> None:
        """یادآورهای آینده را بعد از ری‌استارت دوباره زمان‌بندی کن."""
        if not STORE.exists():
            return
        now = datetime.now(ZoneInfo(settings.timezone))
        kept: list[str] = []
        for line in STORE.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            dt = _parse_dt(rec.get("at"))
            if dt is None or dt <= now:
                continue
            self.scheduler.add_job(
                self._fire, "date", run_date=dt,
                kwargs={"chat_id": rec["chat_id"], "text": rec["text"]},
            )
            kept.append(line)
        STORE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
