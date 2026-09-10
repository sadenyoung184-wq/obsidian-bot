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


REPEAT_WORDS = ("هر روز", "روزانه", "every day", "daily",
                "هر هفته", "هفتگی", "weekly")

FA_REPEAT_WORDS = ("هر روز", "روزانه", "هر هفته", "هفتگی")


def detect_repeat(text: str) -> str | None:
    """اگر متن یادآور تکرارشونده است، نوعش را برگردان: daily | weekly | None."""
    t = (text or "")
    if "هر روز" in t or "روزانه" in t or "every day" in t.lower() or "daily" in t.lower():
        return "daily"
    if "هر هفته" in t or "هفتگی" in t or "weekly" in t.lower():
        return "weekly"
    return None


class ReminderService:
    def __init__(self, bot, vault):
        self.bot = bot
        self.vault = vault
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    # ---------- API ----------

    def schedule(self, chat_id: int, text: str, remind_at: str | None,
                   repeat: str | None = None) -> bool:
        """یادآور جدید ثبت کن. اگر زمان معتبر نباشد False برمی‌گرداند.
        repeat: None | "daily" | "weekly" — اگر None باشد از متن حدس زده می‌شود."""
        dt = _parse_dt(remind_at)
        if dt is None or dt <= datetime.now(ZoneInfo(settings.timezone)):
            return False
        rep = repeat or detect_repeat(text)
        job_id = f"{chat_id}-{int(dt.timestamp())}-{abs(hash(text)) % 10_000}"
        if rep in ("daily", "weekly"):
            # تکرارشونده: هر روز / هر هفته سر همان ساعت
            self.scheduler.add_job(
                self._fire,
                "cron",
                hour=dt.hour, minute=dt.minute,
                day_of_week="*" if rep == "daily" else dt.strftime("%a").lower()[:3],
                id=f"{job_id}-{rep}",
                replace_existing=True,
                kwargs={"chat_id": chat_id, "text": f"{text} (🔁 {'روزانه' if rep == 'daily' else 'هفتگی'})"},
            )
            self._persist(chat_id, text, dt, repeat=rep)
            log.info("Repeating reminder (%s) for %s", rep, text[:40])
            return True
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

    def list_upcoming(self, chat_id: int | None = None) -> list[dict]:
        """یادآورهای آینده (برای گزارش صبحگاهی و /reminders)."""
        out = []
        for job in self.scheduler.get_jobs():
            args = job.kwargs or {}
            if chat_id is not None and args.get("chat_id") != chat_id:
                continue
            if "ReminderService._fire" not in str(job.func):
                continue
            out.append({"id": job.id, "text": str(args.get("text", "")),
                        "next": job.next_run_time.isoformat() if job.next_run_time else "?",
                        "repeat": "🔁" if job.trigger.__class__.__name__ == "CronTrigger" else "⏰"})
        out.sort(key=lambda r: r["next"])
        return out

    def cancel(self, job_id: str) -> bool:
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            return False
        # از فایل هم پاک کن
        if STORE.exists():
            kept = []
            for line in STORE.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    stamp = str(int(_parse_dt(rec.get("at")).timestamp()))
                    if stamp not in job_id:
                        kept.append(line)
                except (json.JSONDecodeError, ValueError, AttributeError, TypeError):
                    continue
            STORE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        return True

    def _persist(self, chat_id: int, text: str, dt: datetime, repeat: str | None = None) -> None:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        record = {"chat_id": chat_id, "text": text, "at": dt.isoformat()}
        if repeat:
            record["repeat"] = repeat
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
            rep = rec.get("repeat")
            if rep in ("daily", "weekly"):
                # تکرارشونده‌ها تاریخ انقضا ندارند — همیشه برگردان
                self.scheduler.add_job(
                    self._fire, "cron",
                    hour=dt.hour if dt else 8, minute=dt.minute if dt else 0,
                    day_of_week="*" if rep == "daily" else (dt.strftime("%a").lower()[:3] if dt else "sat"),
                    kwargs={"chat_id": rec["chat_id"],
                            "text": f"{rec['text']} (🔁 {'روزانه' if rep == 'daily' else 'هفتگی'})"},
                )
                kept.append(line)
                continue
            if dt is None or dt <= now:
                continue
            self.scheduler.add_job(
                self._fire, "date", run_date=dt,
                kwargs={"chat_id": rec["chat_id"], "text": rec["text"]},
            )
            kept.append(line)
        STORE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
