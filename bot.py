"""نقطه ورود ربات: تلگرام + Gemini + Obsidian + یادآور."""
from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

import brain
from config import settings
from reminders import ReminderService
from vault import ObsidianVault

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("obsidian-bot")

vault = ObsidianVault()
bot = Bot(token=settings.telegram_token)
dp = Dispatcher()
reminders = ReminderService(bot, vault)


def _allowed(message: Message) -> bool:
    if not settings.allowed_user_ids:
        return True
    return (message.from_user is not None
            and message.from_user.id in settings.allowed_user_ids)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "سلام! من دستیار هوشمندت هستم 🤖\n\n"
        "هر چیزی بفرست — جزوه کلاس، قرار، ایده، کار، یادآوری — "
        "خودم دسته‌بندی می‌کنم و توی والت Obsidian ذخیره‌اش می‌کنم، "
        "به نوت‌های قدیمی لینکش می‌کنم و اگه لازم باشه سر ساعت یادآوری می‌فرستم.\n\n"
        "دستورات:\n"
        "/todo — نمایش کارهای باز\n"
        "/done <شماره> — تیک زدن کار\n"
        "/today — خلاصه ترکر امروز\n"
        "/help — راهنما"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "مثال‌هایی که می‌فهمم:\n\n"
        "• «جزوه کلاس فیزیک: قانون نیوتن ...» → پوشه Classes\n"
        "• «جلسه با سارا فردا ساعت ۱۰» → پوشه Events + ثبت تاریخ\n"
        "• «باید مقاله را تا جمعه تمام کنم» → تودو + Tasks\n"
        "• «یادم بنداز فردا ساعت ۸ قرص بخورم» → یادآوری تلگرام + Reminders\n"
        "• «ایده: پادکست درباره بهره‌وری» → پوشه Ideas\n"
        "• «امروز خیلی روز خوبی بود ...» → ژورنال + ترکر\n\n"
        "عکس هم می‌توانی بفرستی (با کپشن) — داخل نوت ذخیره می‌شود.\n"
        "ویس هم می‌توانی بفرستی — متنش استخراج و ذخیره می‌شود."
    )


@dp.message(Command("todo"))
async def cmd_todo(message: Message):
    todos = vault.list_open_todos()
    if not todos:
        await message.answer("کاری نداری، همه‌چیز مرتبه ✨")
        return
    lines = [f"{i}. {t[6:]}" for i, t in enumerate(todos, 1)]
    await message.answer("📋 کارهای باز:\n\n" + "\n".join(lines) + "\n\nبرای تیک زدن: /done شماره")


@dp.message(Command("done"))
async def cmd_done(message: Message):
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("مثال: /done 2")
        return
    ok = vault.complete_todo(int(parts[1]))
    await message.answer("انجام شد ✅" if ok else "چنین کاری پیدا نکردم.")


@dp.message(Command("today"))
async def cmd_today(message: Message):
    day = vault.now().strftime("%Y-%m-%d")
    path = vault.root / "Tracker" / f"{day}.md"
    if not path.exists():
        await message.answer("امروز هنوز چیزی در ترکر ثبت نشده.")
        return
    text = path.read_text(encoding="utf-8")
    await message.answer(text[:4000])


@dp.message(F.voice | F.audio)
async def handle_voice(message: Message):
    if not _allowed(message):
        return
    status = await message.answer("🎧 دارم ویس را گوش می‌دهم...")
    try:
        file_id = message.voice.file_id if message.voice else message.audio.file_id
        buf = io.BytesIO()
        await bot.download(file_id, destination=buf)
        buf.seek(0)
        text = _transcribe(buf.read())
    except Exception as exc:
        log.warning("voice failed: %s", exc)
        await status.edit_text("نتوانستم ویس را پردازش کنم. متنش را تایپ کن.")
        return
    await status.delete()
    await _process_text(message, text or "(ویس بدون متن)")


@dp.message(F.photo)
async def handle_photo(message: Message):
    if not _allowed(message):
        return
    caption = message.caption or "عکس بدون کپشن"
    # ذخیره عکس داخل والت
    assets = vault.root / "Assets"
    assets.mkdir(parents=True, exist_ok=True)
    photo = message.photo[-1]
    ext = ".jpg"
    fname = f"{vault.now().strftime('%Y-%m-%d-%H%M%S')}{ext}"
    dest = assets / fname
    await bot.download(photo.file_id, destination=dest)
    await _process_text(message, f"{caption}\n\n![[{dest.relative_to(vault.root).as_posix()}]]")


@dp.message(F.text)
async def handle_text(message: Message):
    if not _allowed(message):
        await message.answer("⛔ این ربات خصوصی است.")
        return
    await _process_text(message, message.text or "")


async def _process_text(message: Message, text: str) -> None:
    status = await message.answer("🧠 دارم فکر می‌کنم...")
    try:
        result = await asyncio.to_thread(brain.analyze, text)
        path = await asyncio.to_thread(vault.save_note, result)

        extra = ""
        if result.get("remind_at"):
            ok = reminders.schedule(
                chat_id=message.chat.id,
                text=f"{result['title']}\n{result['body'][:300]}",
                remind_at=result["remind_at"],
            )
            extra = "\n⏰ یادآوری تنظیم شد." if ok else "\n⚠️ زمان یادآوری معتبر نبود؛ فقط ذخیره شد."

        rel = path.parent.name
        await status.edit_text(
            f"{result.get('reply', 'ذخیره شد ✅')}{extra}\n\n📁 {rel} → `{path.name}`",
            parse_mode="Markdown",
        )
    except Exception as exc:
        log.exception("process failed")
        await status.edit_text(f"خطایی پیش آمد ولی پیامت گم نشد: {exc}")


def _transcribe(audio_bytes: bytes) -> str:
    """رونویسی ویس با خود Gemini (ورودی صوتی)."""
    from google import genai as genai_new
    from google.genai import types as genai_types_new
    client = genai_new.Client(api_key=settings.gemini_api_key)
    resp = client.models.generate_content(
        model=settings.gemini_model,
        contents=[
            genai_types_new.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg"),
            "این ویس فارسی را دقیق رونویسی کن. فقط متن رونویسی را برگردان.",
        ],
    )
    return (resp.text or "").strip()


async def main() -> None:
    problems = settings.validate()
    if problems:
        raise SystemExit("❌ " + "\n".join(problems))
    # اطمینان از وجود والت (+ اتصال به گیت اگر فعال باشد)
    Path(settings.vault_path).mkdir(parents=True, exist_ok=True)
    try:
        from git_sync import ensure_repo
        ensure_repo(Path(settings.vault_path).expanduser())
    except Exception:
        log.warning("git init failed, continuing locally", exc_info=True)
    reminders.start()
    log.info("Bot started. Vault: %s", settings.vault_path)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
