"""مغز ربات: اتصال به Google Gemini برای فهم پیام و تصمیم‌گیری.

خروجی همیشه یک دیکشنری استاندارد است تا بقیه ماژول‌ها راحت کار کنند،
حتی اگر API قطع باشد (حالت fallback).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # اجازه بده حالت fallback بدون نصب پکیج هم کار کند
    genai = None  # type: ignore
    genai_types = None  # type: ignore
from zoneinfo import ZoneInfo

from config import settings

log = logging.getLogger(__name__)

# حافظه والت (lazy import تا تست‌ها بدون والت هم کار کنند)
_vault_memory_provider = None


def set_memory_provider(fn) -> None:
    """bot.py این را ست می‌کند تا brain به حافظه والت دسترسی داشته باشد."""
    global _vault_memory_provider
    _vault_memory_provider = fn


def _vault_memory() -> str:
    if _vault_memory_provider is None:
        return "حافظه والت در دسترس نیست."
    try:
        return _vault_memory_provider() or "والت هنوز نوتی ندارد."
    except Exception as exc:
        log.warning("memory provider failed: %s", exc)
        return "حافظه والت در دسترس نیست."

SYSTEM_PROMPT = """تو دستیار هوشمند یک کاربر فارسی‌زبان هستی که پیام‌هایش را به یادداشت‌های Obsidian تبدیل می‌کنی.

برای هر پیام، فقط و فقط یک JSON معتبر با همین کلیدها برگردان (بدون توضیح اضافه، بدون ```):
{
  "type": "class" | "event" | "review" | "task" | "reminder" | "idea" | "log" | "question",
  "title": "عنوان کوتاه فارسی برای نوت",
  "folder": "Classes" | "Events" | "Reviews" | "Tasks" | "Reminders" | "Ideas" | "Journal" | "Inbox",
  "body": "متن تمیز و ساخت‌یافته به مارک‌داون فارسی",
  "tags": ["تگ۱", "تگ۲"],
  "keywords": ["کلمات کلیدی برای لینک‌سازی به نوت‌های قدیمی"],
  "due": "تاریخ سررسید ISO مثل 2026-09-10T08:00:00 یا null",
  "remind_at": "زمان یادآوری ISO مثل 2026-09-10T08:00:00 یا null",
  "tracker": true یا false (آیا در فایل ترکر روزانه هم ثبت شود؟),
  "reply": "پاسخ کوتاه و صمیمی به کاربر به فارسی"
}

راهنما:
- class: جزوه، درس، کلاس، آموزش → folder=Classes
- event: جلسه، قرار، همایش، رویداد با تاریخ → folder=Events، due=تاریخ رویداد
- review: مرور، جمع‌بندی، بازبینی → folder=Reviews
- task: کار، باید انجام شود، تودو → folder=Tasks، due اگر تاریخ دارد
- reminder: «یادم بنداز»، «یادآوری کن» → folder=Reminders، remind_at حتماً پر شود
- idea: ایده، به ذهنم رسید → folder=Ideas
- log: خاطره روزانه، اتفاق امروز، حس و حال → folder=Journal، tracker=true
- question: سوال از تو → folder=Inbox، body=خلاصه سوال و جواب، و جواب کامل را در reply بنویس
- زمان اکنون: {now} (منطقه {tz}). تاریخ‌های نسبی مثل «فردا ساعت ۸ صبح» را نسبت به همین زمان به ISO تبدیل کن.
- body را با تیتر، بولت و مرتب بنویس. ایموجی نزن.
- keywords: ۳ تا ۷ کلمه مهم پیام برای پیدا کردن نوت‌های مرتبط قدیمی.
- اگر پیام به نوت خاصی اشاره دارد (اسم، موضوع)، حتماً همان path را در related_paths بگذار.
- فقط پوشه‌های موجود را پیشنهاد بده؛ اگر موضوع به هیچ‌کدام نمی‌خورد folder را Inbox بگذار.

## حافظه والت (کل نوت‌های کاربر — حتماً بخوان و استفاده کن)
{memory}

برای هر پیام، فقط و فقط یک JSON معتبر با همین کلیدها برگردان (بدون توضیح اضافه، بدون ```):
{
  "type": "class" | "event" | "review" | "task" | "reminder" | "idea" | "log" | "question" | "manage" | "chat",
  "title": "عنوان کوتاه فارسی برای نوت",
  "folder": "Classes" | "Events" | "Reviews" | "Tasks" | "Reminders" | "Ideas" | "Journal" | "Inbox",
  "body": "متن تمیز و ساخت‌یافته به مارک‌داون فارسی",
  "tags": ["تگ۱", "تگ۲"],
  "keywords": ["کلمات کلیدی برای لینک‌سازی به نوت‌های قدیمی"],
  "related_paths": ["مسیر دقیق نوت‌های مرتبط از حافظه، مثل Journal/2026-09-10-xxx.md"],
  "due": "تاریخ سررسید ISO مثل 2026-09-10T08:00:00 یا null",
  "remind_at": "زمان یادآوری ISO مثل 2026-09-10T08:00:00 یا null",
  "tracker": true یا false (آیا در فایل ترکر روزانه هم ثبت شود؟),
  "reply": "پاسخ کوتاه و صمیمی به کاربر به فارسی",
  "manage_op": "اگر type=manage است: یکی از read | write | append | rename | move | delete | mkdir | rmdir | list، وگرنه null",
  "manage_args": "اگر type=manage است: آبجکت آرگومان‌ها (path, content, new_path, folder)، وگرنه null"
}

انواع جدید:
- manage: دستور مدیریتی والت («این نوت را پاک کن»، «پوشه X بساز»، «متن Y را به نوت Z اضافه کن»، «نوت را به پوشه A منتقل کن»). manage_op و manage_args را دقیق پر کن.
- chat: گفت‌وگوی عادی یا نظر درباره پیش‌نویس («خوبه»، «عنوان را عوض کن»، «نه، پوشه را عوض کن»). چیزی ذخیره نکن؛ فقط در reply جواب بده.
"""

_client = None

# مدل‌های Gemini مرتب بازنشسته می‌شوند؛ به ترتیب اولویت امتحان کن
# تا با 404 خوردن یکی، بعدی خودکار تست شود و ربات روی fallback نماند.
MODEL_CANDIDATES = [
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.0-flash-latest",
    "gemini-2.0-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash",
]


def _candidate_models() -> list[str]:
    """مدل تنظیم‌شده اول، بعد بقیه کاندیداها (بدون تکرار)."""
    ordered = [settings.gemini_model] if settings.gemini_model else []
    for m in MODEL_CANDIDATES:
        if m not in ordered:
            ordered.append(m)
    return ordered


def _system_text() -> str:
    # NOTE: از replace استفاده می‌کنیم نه format — چون خود پرامپت نمونه JSON
    # با آکولاد دارد و format آن‌ها را با placeholder اشتباه می‌گیرد
    # (همین باگ باعث KeyError روی "type" و fallback دائمی بود).
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="minutes")
    return (SYSTEM_PROMPT
            .replace("{now}", now)
            .replace("{tz}", settings.timezone)
            .replace("{memory}", _vault_memory()))


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _strip_fences(raw: str) -> str:
    """حذف ```json ... ``` که بعضی مدل‌ها دور JSON می‌گذارند."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _generate_text(contents, json_mode: bool):
    """اولین مدل سالمی که جواب داد را برگردان؛ فقط 404 مدل بعدی را امتحان می‌کند."""
    last_exc: Exception | None = None
    for model in _candidate_models():
        try:
            resp = _get_client().models.generate_content(
                model=model,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    system_instruction=_system_text(),
                    **({"response_mime_type": "application/json"} if json_mode else {}),
                ),
            )
            if model != settings.gemini_model:
                log.info("Using fallback model: %s", model)
            return resp.text or ""
        except Exception as exc:
            if "404" in str(exc) or "NOT_FOUND" in str(exc):
                log.warning("Model %s not available, trying next", model)
                last_exc = exc
                continue
            raise
    raise last_exc or RuntimeError("no Gemini model available")


def analyze(text: str) -> dict:
    """پیام کاربر را تحلیل کن و دیکشنری استاندارد برگردان."""
    fallback = _fallback(text)
    if not settings.gemini_api_key or genai is None:
        return fallback
    try:
        raw = _generate_text(text, json_mode=True)
        data = json.loads(_strip_fences(raw), strict=False)
        if not isinstance(data, dict):
            raise ValueError(f"expected JSON object, got: {str(data)[:100]}")
        return _normalize(data, text)
    except Exception as exc:  # قطع بودن API نباید ربات را بخواباند
        log.warning("Gemini failed, using fallback: %s", exc)
        return fallback


def revise_draft(draft_result: dict, raw_text: str, feedback: str) -> dict | None:
    """نظر کاربر را روی آخرین پیش‌نویس اعمال کن. خروجی: نتیجه به‌روزشده یا None."""
    if genai is None or not settings.gemini_api_key:
        return None
    prompt = (
        "یک پیش‌نویس نوت Obsidian هست که کاربر درباره‌اش نظر داده. "
        "پیش‌نویس به‌روزشده را با همان فرمت JSON قبلی برگردان (فقط JSON، بدون ```).\n\n"
        f"متن اصلی کاربر: {raw_text}\n\n"
        f"پیش‌نویس فعلی: {json.dumps(draft_result, ensure_ascii=False)}\n\n"
        f"نظر جدید کاربر: {feedback}\n\n"
        "اگر نظر ربطی به پیش‌نویس ندارد، دقیقاً همین را برگردان: {\"unrelated\": true}"
    )
    try:
        raw = _generate_text(prompt, json_mode=True)
        data = json.loads(_strip_fences(raw), strict=False)
        if not isinstance(data, dict) or data.get("unrelated"):
            return None
        merged = dict(draft_result)
        for key in ("title", "folder", "body", "tags", "keywords", "related_paths",
                    "due", "remind_at", "tracker", "reply", "type"):
            if key in data and data[key] not in (None, "", []):
                merged[key] = data[key]
        return _normalize(merged, raw_text)
    except Exception as exc:
        log.warning("revise failed: %s", exc)
        return None


IMAGE_PROMPT = """این تصویر را برای یک کاربر فارسی‌زبان تحلیل کن.

اگر تصویر متن دارد (جزوه، اسلاید، برگه امتحان، تابلو، نسخه، رسید):
کل متن را دقیق رونویسی کن (OCR) — ساختار و تیترها حفظ شود.

بعد فقط و فقط یک JSON معتبر با همین کلیدها برگردان (بدون توضیح اضافه، بدون ```):
{
  "title": "عنوان کوتاه فارسی",
  "folder": "Classes" | "Events" | "Reviews" | "Tasks" | "Reminders" | "Ideas" | "Journal" | "Inbox",
  "body": "متن کامل رونویسی‌شده + خلاصه ساخت‌یافته به مارک‌داون فارسی",
  "tags": ["تگ۱", "تگ۲"],
  "keywords": ["کلمات کلیدی برای لینک‌سازی"],
  "related_paths": ["مسیر نوت‌های مرتبط از حافظه"],
  "ocr_text": "متن خام رونویسی (اگر متنی نیست، رشته خالی)",
  "has_text": true یا false (آیا تصویر متن قابل رونویسی دارد؟),
  "tracker": true یا false,
  "reply": "پاسخ کوتاه و صمیمی به فارسی: متن بود یا نه و چه چیزی ذخیره شد"
}

راهنما:
- جزوه/درس/آموزش → folder=Classes
- برگه امتحان/کارنامه → folder=Reviews
- تابلو اعلانات/پوستر رویداد → folder=Events
- دست‌نوشته روزانه/خاطره → folder=Journal
- اگر تصویر متن ندارد (منظره، عکس شخصی) → body=توصیف کوتاه تصویر، has_text=false
- کپشن کاربر: {caption}
"""


def describe_image(image_bytes: bytes, mime_type: str, caption: str = "") -> dict | None:
    """تحلیل تصویر + OCR با Gemini. خروجی: دیکشنری نرمال‌شده یا None."""
    if genai is None or not settings.gemini_api_key:
        return None
    from google.genai import types as _types
    prompt = IMAGE_PROMPT.replace("{caption}", caption or "بدون کپشن")
    last_exc: Exception | None = None
    for model in _candidate_models():
        try:
            resp = _get_client().models.generate_content(
                model=model,
                contents=[
                    _types.Part.from_bytes(data=image_bytes, mime_type=mime_type or "image/jpeg"),
                    prompt,
                ],
                config=genai_types.GenerateContentConfig(
                    system_instruction=_system_text(),
                    response_mime_type="application/json",
                ),
            )
            data = json.loads(_strip_fences(resp.text or ""), strict=False)
            if not isinstance(data, dict):
                raise ValueError("expected JSON object from image analysis")
            out = _normalize({
                "type": "log",
                "title": data.get("title") or (caption or "عکس")[:40],
                "folder": data.get("folder") or "Inbox",
                "body": data.get("body") or "",
                "tags": data.get("tags") or [],
                "keywords": data.get("keywords") or [],
                "related_paths": data.get("related_paths") or [],
                "due": None, "remind_at": None,
                "tracker": bool(data.get("tracker", False)),
                "reply": data.get("reply") or "ذخیره شد ✅",
            }, caption or "عکس")
            out["ocr_text"] = str(data.get("ocr_text") or "")
            out["has_text"] = bool(data.get("has_text", False))
            out["type"] = _image_type(out)
            return out
        except Exception as exc:
            if "404" in str(exc) or "NOT_FOUND" in str(exc):
                log.warning("Model %s not available for image, trying next", model)
                last_exc = exc
                continue
            log.warning("Image analysis failed: %s", exc)
            return None
    log.warning("Image analysis failed: %s", last_exc)
    return None


def _image_type(out: dict) -> str:
    folder = out.get("folder")
    return {"Classes": "class", "Events": "event", "Reviews": "review",
            "Tasks": "task", "Reminders": "reminder", "Ideas": "idea",
            "Journal": "log"}.get(folder, "log")


def ask(question: str) -> str:
    """گفت‌وگوی آزاد (برای type=question هم استفاده می‌شود)."""
    if genai is None or not settings.gemini_api_key:
        return "الان به هوش مصنوعی دسترسی ندارم، ولی پیامت را در Inbox ذخیره کردم. بعداً دوباره بپرس."
    try:
        return _generate_text(
            "به این سوال به فارسی، کوتاه و کاربردی جواب بده:\n" + question,
            json_mode=False,
        ).strip()
    except Exception as exc:
        log.warning("Gemini ask failed: %s", exc)
        return "الان به هوش مصنوعی دسترسی ندارم، ولی پیامت را در Inbox ذخیره کردم. بعداً دوباره بپرس."


def _normalize(data: dict, original: str) -> dict:
    valid_types = {"class", "event", "review", "task", "reminder", "idea", "log",
                   "question", "manage", "chat"}
    valid_folders = {"Classes", "Events", "Reviews", "Tasks", "Reminders", "Ideas", "Journal", "Inbox"}
    manage_ops = {"read", "write", "append", "rename", "move", "delete", "mkdir", "rmdir", "list"}
    op = data.get("manage_op") if data.get("manage_op") in manage_ops else None
    args = data.get("manage_args") if isinstance(data.get("manage_args"), dict) else None
    out = {
        "type": data.get("type") if data.get("type") in valid_types else "log",
        "title": str(data.get("title") or original[:40]).strip(),
        "folder": data.get("folder") if data.get("folder") in valid_folders else "Inbox",
        "body": str(data.get("body") or original).strip(),
        "tags": [str(t) for t in (data.get("tags") or [])][:6],
        "keywords": [str(k) for k in (data.get("keywords") or [])][:8],
        "related_paths": [str(p) for p in (data.get("related_paths") or [])][:5],
        "due": data.get("due"),
        "remind_at": data.get("remind_at"),
        "tracker": bool(data.get("tracker", False)),
        "reply": str(data.get("reply") or "ذخیره شد ✅").strip(),
        "manage_op": op,
        "manage_args": args or {},
    }
    # manage بدون عملیات معتبر → به chat تبدیل کن تا چیزی خراب نشود
    if out["type"] == "manage" and not op:
        out["type"] = "chat"
    return out


REMIND_WORDS = ("یادم بنداز", "یادآوری", "یادم بیار", "ریمایندر", "remind")
TASK_WORDS = ("باید", "انجام بدم", "تودو", "todo", "کار دارم", "تسک")


def _fallback(text: str) -> dict:
    """تصمیم ساده بدون API — بر اساس کلمات کلیدی."""
    t = text
    if any(w in t for w in REMIND_WORDS):
        typ, folder = "reminder", "Reminders"
    elif any(w in t for w in TASK_WORDS):
        typ, folder = "task", "Tasks"
    elif "کلاس" in t or "درس" in t or "جزوه" in t:
        typ, folder = "class", "Classes"
    elif "جلسه" in t or "قرار" in t or "همایش" in t:
        typ, folder = "event", "Events"
    elif "مرور" in t or "جمع‌بندی" in t or "بازبینی" in t:
        typ, folder = "review", "Reviews"
    elif "ایده" in t or "به ذهنم" in t:
        typ, folder = "idea", "Ideas"
    else:
        typ, folder = "log", "Journal"
    words = re.findall(r"[؀-ۿ\w]{3,}", t)
    return {
        "type": typ,
        "title": t[:40],
        "folder": folder,
        "body": t,
        "tags": [],
        "keywords": words[:7],
        "due": None,
        "remind_at": None,
        "tracker": typ == "log",
        "reply": "ذخیره شد ✅ (بدون اتصال AI، دسته‌بندی حدسی است)",
    }
