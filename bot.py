"""نقطه ورود ربات: تلگرام + Gemini + حافظه والت + پیش‌نویس + مدیریت والت."""
from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

import brain
from brain import set_memory_provider
from config import settings
from drafts import DraftStore, MODE_AUTO, MODE_DRAFT
from memory import memory_context, refresh_index
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
drafts = DraftStore()

# حافظه والت را به brain وصل کن تا در هر تصمیم کل والت را ببیند
set_memory_provider(lambda: memory_context(vault.root))


def _allowed(message: Message) -> bool:
    if not settings.allowed_user_ids:
        return True
    return (message.from_user is not None
            and message.from_user.id in settings.allowed_user_ids)


def _draft_preview(result: dict, num: int) -> str:
    kind = {"task": "کار", "reminder": "یادآوری", "class": "کلاس",
            "event": "رویداد", "review": "مرور", "idea": "ایده",
            "log": "ژورنال", "question": "سوال"}.get(result.get("type"), "نوت")
    lines = [
        f"📝 پیش‌نویس {num} ({kind} → {result.get('folder')}):",
        f"عنوان: {result.get('title')}",
        f"{(result.get('body') or '')[:400]}",
    ]
    if result.get("related_paths"):
        links = "، ".join(result["related_paths"][:3])
        lines.append(f"🔗 مرتبط: {links}")
    lines.append("\nنظر بده (مثلاً «عنوان را عوض کن») یا پیام بعدی را بفرست. برای ثبت نهایی: /done")
    return "\n".join(lines)


def _commit_result(chat_id: int, result: dict) -> tuple[str, str]:
    """یک نتیجه تاییدشده را واقعاً ذخیره کن. خروجی: (نام فایل، خط یادآور)."""
    path = vault.save_note(result)
    try:
        refresh_index(vault.root)
    except Exception:
        pass
    extra = ""
    if result.get("remind_at"):
        ok = reminders.schedule(
            chat_id=chat_id,
            text=f"{result['title']}\n{(result.get('body') or '')[:300]}",
            remind_at=result["remind_at"],
        )
        extra = " ⏰ یادآوری تنظیم شد." if ok else " ⚠️ زمان یادآوری معتبر نبود؛ فقط ذخیره شد."
    return path.name, extra


# ---------- دستورات ----------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "سلام! من دستیار هوشمندت هستم 🤖\n\n"
        "حالا با حافظه کامل والت کار می‌کنم — همه نوت‌هایت را می‌شناسم.\n\n"
        "📝 حالت پیش‌نویس (فعال): هر پیامی بفرستی اول پیش‌نویس می‌شود؛ "
        "نظر بده و در آخر با /done همه را یکجا ثبت کن.\n"
        "⚡ با /auto به حالت ذخیره فوری برمی‌گردی، با /draft دوباره پیش‌نویس.\n\n"
        "دستورات:\n"
        "/todo — کارهای باز\n"
        "/done [شماره] — تیک تودو / ثبت نهایی پیش‌نویس‌ها\n"
        "/cancel — دور ریختن پیش‌نویس‌ها\n"
        "/drafts — دیدن پیش‌نویس‌های باز\n"
        "/auto — حالت ذخیره فوری\n"
        "/draft — حالت پیش‌نویس\n"
        "/notes [پوشه] — فهرست نوت‌ها\n"
        "/read <مسیر> — خواندن نوت\n"
        "/today — ترکر امروز\n"
        "/memory — بازسازی حافظه والت\n"
        "/help — راهنما"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "مثال‌ها:\n\n"
        "• «جزوه کلاس فیزیک: قانون نیوتن ...» → پیش‌نویس در Classes\n"
        "• «جلسه با سارا فردا ساعت ۱۰» → پیش‌نویس Events + تاریخ\n"
        "• «یادم بنداز فردا ساعت ۸ قرص بخورم» → پیش‌نویس + یادآوری (بعد از /done)\n"
        "• «نوت X را پاک کن» → اول نشان می‌دهم، با تایید حذف می‌شود\n"
        "• «پوشه Dreams بساز» → با تایید ساخته می‌شود\n"
        "• «نوت X را به پوشه Y منتقل کن» → مدیریتی با تایید\n\n"
        "عکس (با کپشن) و ویس هم مثل قبل کار می‌کند."
    )


@dp.message(Command("auto"))
async def cmd_auto(message: Message):
    drafts.set_mode(message.chat.id, MODE_AUTO)
    await message.answer("⚡ حالت ذخیره فوری فعال شد. هر پیام مستقیم ذخیره می‌شود.")


@dp.message(Command("draft"))
async def cmd_draft(message: Message):
    drafts.set_mode(message.chat.id, MODE_DRAFT)
    await message.answer("📝 حالت پیش‌نویس فعال شد. اول نظر بده، بعد با /done ثبت کن.")


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message):
    n = drafts.clear(message.chat.id)
    await message.answer(f"🗑 {n} پیش‌نویس دور ریخته شد." if n else "پیش‌نویسی نبود.")


@dp.message(Command("drafts"))
async def cmd_drafts(message: Message):
    items = drafts.list(message.chat.id)
    if not items:
        await message.answer("پیش‌نویسی نداری.")
        return
    lines = [f"{i}. [{r['result'].get('folder')}] {r['result'].get('title')}"
             for i, r in enumerate(items, 1)]
    await message.answer("📝 پیش‌نویس‌ها:\n\n" + "\n".join(lines)
                         + "\n\nثبت نهایی: /done | انصراف: /cancel")


@dp.message(Command("todo"))
async def cmd_todo(message: Message):
    todos = vault.list_open_todos()
    if not todos:
        await message.answer("کاری نداری، همه‌چیز مرتبه ✨")
        return
    lines = [f"{i}. {t[6:]}" for i, t in enumerate(todos, 1)]
    await message.answer("📋 کارهای باز:\n\n" + "\n".join(lines) + "\n\nتیک تودو: /done شماره (وقتی پیش‌نویسی نداری)")


@dp.message(Command("done"))
async def cmd_done(message: Message):
    parts = (message.text or "").split()
    # /done 3 → همیشه تیک تودو (حتی اگر پیش‌نویس باز باشد)
    if len(parts) >= 2 and parts[1].isdigit():
        ok = vault.complete_todo(int(parts[1]))
        await message.answer("انجام شد ✅" if ok else "چنین کاری پیدا نکردم.")
        return
    # /done خالی → ثبت نهایی همه پیش‌نویس‌ها
    if drafts.list(message.chat.id):
        await _commit_all(message)
        return
    await message.answer("پیش‌نویسی نداری. تیک تودو: /done شماره")


async def _commit_all(message: Message) -> None:
    items = drafts.list(message.chat.id)
    if not items:
        await message.answer("پیش‌نویسی نداری.")
        return
    status = await message.answer(f"⏳ ثبت {len(items)} مورد...")
    done, reminds, managed = 0, 0, 0
    for item in items:
        try:
            res = item["result"]
            if res.get("type") == "manage":
                out = await asyncio.to_thread(
                    _run_manage_op, res.get("manage_op"), res.get("manage_args") or {})
                await message.answer(out[:4000])
                managed += 1
            else:
                name, extra = await asyncio.to_thread(_commit_result, message.chat.id, res)
                done += 1
                if "یادآوری تنظیم شد" in extra:
                    reminds += 1
        except Exception as exc:
            log.exception("commit failed")
            await message.answer(f"⚠️ یکی ثبت نشد: {exc}")
    drafts.clear(message.chat.id)
    bits = []
    if done:
        bits.append(f"{done} نوت ثبت شد")
    if managed:
        bits.append(f"{managed} دستور مدیریتی اجرا شد")
    if reminds:
        bits.append(f"{reminds} یادآوری فعال")
    await status.edit_text("✅ " + "، ".join(bits) + "." if bits else "چیزی ثبت نشد.")


@dp.message(Command("today"))
async def cmd_today(message: Message):
    day = vault.now().strftime("%Y-%m-%d")
    path = vault.root / "Tracker" / f"{day}.md"
    if not path.exists():
        await message.answer("امروز هنوز چیزی در ترکر ثبت نشده.")
        return
    text = path.read_text(encoding="utf-8")
    await message.answer(text[:4000])


@dp.message(Command("memory"))
async def cmd_memory(message: Message):
    await message.answer("🧠 دارم حافظه را بازسازی می‌کنم...")
    try:
        index = await asyncio.to_thread(refresh_index, vault.root)
        await message.answer(f"✅ حافظه به‌روز شد: {len(index.get('notes', []))} نوت.")
    except Exception as exc:
        await message.answer(f"خطا در بازسازی حافظه: {exc}")


@dp.message(Command("notes"))
async def cmd_notes(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    folder = parts[1].strip() if len(parts) > 1 else None
    items = await asyncio.to_thread(vault.list_notes, folder)
    if not items:
        await message.answer("نوتی پیدا نکردم.")
        return
    await message.answer("📚 نوت‌ها:\n\n" + "\n".join(f"- `{p}`" for p in items[:40]))


@dp.message(Command("read"))
async def cmd_read(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("مثال: /read Journal/2026-09-10-xxx.md")
        return
    text = await asyncio.to_thread(vault.read_note, parts[1].strip())
    if text is None:
        await message.answer("چنین نوتی پیدا نکردم.")
        return
    await message.answer(text[:4000])


# ---------- پیام‌های عادی ----------

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
    assets = vault.root / "Assets"
    assets.mkdir(parents=True, exist_ok=True)
    photo = message.photo[-1]
    fname = f"{vault.now().strftime('%Y-%m-%d-%H%M%S')}.jpg"
    dest = assets / fname
    await bot.download(photo.file_id, destination=dest)
    await _process_text(message, f"{caption}\n\n![[{dest.relative_to(vault.root).as_posix()}]]")


@dp.message(F.text)
async def handle_text(message: Message):
    if not _allowed(message):
        await message.answer("⛔ این ربات خصوصی است.")
        return
    text = message.text or ""
    # «تایید» با پیش‌نویس باز = ثبت نهایی
    if drafts.list(message.chat.id) and DraftStore.is_confirm(text):
        await _commit_all(message)
        return
    await _process_text(message, text)


async def _process_text(message: Message, text: str) -> None:
    status = await message.answer("🧠 دارم فکر می‌کنم...")
    try:
        # نظر درباره آخرین پیش‌نویس؟ (متن کوتاه + پیش‌نویس باز)
        items = drafts.list(message.chat.id)
        mode = drafts.get_mode(message.chat.id)
        result = await asyncio.to_thread(brain.analyze, text)

        # --- چت عادی: فقط جواب بده ---
        if result.get("type") == "chat":
            reply = result.get("reply", "")
            # اگر پیش‌نویس باز است و نظر درباره آن است، اعمال کن
            if items and len(text) < 300:
                merged = await asyncio.to_thread(
                    brain.revise_draft,
                    items[-1]["result"],
                    items[-1]["raw"],
                    text,
                )
                if merged:
                    drafts.update_last(message.chat.id, merged)
                    await status.edit_text(
                        f"✏️ پیش‌نویس به‌روز شد:\n\n{_draft_preview(merged, len(items))}"
                    )
                    return
            await status.edit_text(reply or "باشه 👍")
            return

        # --- دستور مدیریتی: اول نشان بده، با تایید اجرا کن ---
        if result.get("type") == "manage":
            await _handle_manage(message, status, text, result)
            return

        # --- نوت عادی ---
        if mode == MODE_AUTO or text.startswith("/save "):
            clean = text[6:] if text.startswith("/save ") else text
            if clean != text:
                result = await asyncio.to_thread(brain.analyze, clean)
            name, extra = await asyncio.to_thread(_commit_result, message.chat.id, result)
            rel = result.get("folder", "")
            await status.edit_text(
                f"{result.get('reply', 'ذخیره شد ✅')}{extra}\n\n📁 {rel} → `{name}`",
                parse_mode="Markdown",
            )
        else:
            num = drafts.add(message.chat.id, text, result)
            await status.edit_text(_draft_preview(result, num))
    except Exception as exc:
        log.exception("process failed")
        await status.edit_text(f"خطایی پیش آمد ولی پیامت گم نشد: {exc}")


async def _handle_manage(message: Message, status: Message, text: str, result: dict) -> None:
    op = result.get("manage_op")
    args = result.get("manage_args") or {}
    # اگر تایید صریح در متن است (مثلاً «... را پاک کن، تایید») مستقیم اجرا کن
    force = any(w in text for w in ("تایید می‌کنم", "تایيد می‌کنم", "حتماً انجام بده", "force"))
    plan = _describe_plan(op, args)
    if not force:
        # ذخیره پلن برای تایید بعدی
        drafts.add(message.chat.id, f"[manage] {text}",
                   {"type": "manage", "title": f"مدیریتی: {op}",
                    "folder": "Inbox", "body": plan, "tags": [], "keywords": [],
                    "due": None, "remind_at": None, "tracker": False,
                    "reply": plan, "manage_op": op, "manage_args": args})
        await status.edit_text(
            f"⚠️ این کار را انجام بدهم؟\n\n{plan}\n\n"
            "برای اجرا «تایید» بفرست، برای انصراف /cancel."
        )
        return
    await _execute_manage(message, status, op, args)


def _describe_plan(op: str | None, args: dict) -> str:
    if op == "read":
        return f"📖 خواندن: `{args.get('path', '?')}`"
    if op == "write":
        return f"✍️ ساخت/بازنویسی: `{args.get('path', '?')}`"
    if op == "append":
        return f"➕ افزودن به: `{args.get('path', '?')}`"
    if op == "rename":
        return f"✏️ تغییر نام: `{args.get('path', '?')}` → `{args.get('new_path', '?')}`"
    if op == "move":
        return f"📦 انتقال: `{args.get('path', '?')}` → پوشه `{args.get('folder', '?')}`"
    if op == "delete":
        return f"🗑 حذف (انتقال به سطل): `{args.get('path', '?')}`"
    if op == "mkdir":
        return f"📁 ساخت پوشه: `{args.get('folder', '?')}`"
    if op == "rmdir":
        return f"📁 حذف پوشه خالی: `{args.get('folder', '?')}`"
    if op == "list":
        return f"📚 فهرست: `{args.get('folder', 'کل والت')}`"
    return "عملیات نامشخص."


def _run_manage_op(op: str | None, args: dict) -> str:
    """اجرای همگام یک دستور مدیریتی؛ خروجی متنی برای کاربر. (قابل تست بدون تلگرام)"""
    try:
        if op == "read":
            text = vault.read_note(args.get("path", ""))
            return (text or "خالی یا پیدا نشد.")[:4000]
        if op == "write":
            p = vault.write_note(args.get("path", ""), args.get("content", ""))
            return f"✅ نوشته شد: `{p}`" if p else "❌ مسیر معتبر نیست."
        if op == "append":
            p = vault.append_note(args.get("path", ""), args.get("content", ""))
            return f"✅ اضافه شد به: `{p}`" if p else "❌ نوت پیدا نشد."
        if op == "rename":
            p = vault.rename_note(args.get("path", ""), args.get("new_path", ""))
            return f"✅ تغییر نام: `{p}`" if p else "❌ انجام نشد."
        if op == "move":
            p = vault.move_note(args.get("path", ""), args.get("folder", ""))
            return f"✅ منتقل شد: `{p}`" if p else "❌ انجام نشد."
        if op == "delete":
            dest = vault.delete_note(args.get("path", ""))
            return "🗑 به سطل منتقل شد (قابل بازیابی)." if dest else "❌ انجام نشد."
        if op == "mkdir":
            d = vault.make_folder(args.get("folder", ""))
            return f"✅ پوشه ساخته شد: `{d}`" if d else "❌ نام معتبر نیست."
        if op == "rmdir":
            ok = vault.remove_folder(args.get("folder", ""))
            return "✅ پوشه حذف شد." if ok else "❌ فقط پوشه خالی حذف می‌شود."
        if op == "list":
            items = vault.list_notes(args.get("folder"))
            return "📚\n" + "\n".join(f"- `{p}`" for p in items[:40]) if items else "خالی است."
        return "عملیات نامشخص است."
    except Exception as exc:
        log.exception("manage failed")
        return f"خطا در اجرا: {exc}"


async def _execute_manage(message: Message, status: Message, op: str | None, args: dict) -> None:
    out = await asyncio.to_thread(_run_manage_op, op, args)
    await status.edit_text(out[:4000])


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
    Path(settings.vault_path).mkdir(parents=True, exist_ok=True)
    try:
        from git_sync import ensure_repo
        ensure_repo(Path(settings.vault_path).expanduser())
    except Exception:
        log.warning("git init failed, continuing locally", exc_info=True)
    try:
        await asyncio.to_thread(refresh_index, vault.root)
        log.info("Vault memory indexed")
    except Exception:
        log.warning("memory index failed", exc_info=True)
    reminders.start()
    log.info("Bot started. Vault: %s", settings.vault_path)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
