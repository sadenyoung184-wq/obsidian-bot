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
    return SYSTEM_PROMPT.replace("{now}", now).replace("{tz}", settings.timezone)


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
    valid_types = {"class", "event", "review", "task", "reminder", "idea", "log", "question"}
    valid_folders = {"Classes", "Events", "Reviews", "Tasks", "Reminders", "Ideas", "Journal", "Inbox"}
    out = {
        "type": data.get("type") if data.get("type") in valid_types else "log",
        "title": str(data.get("title") or original[:40]).strip(),
        "folder": data.get("folder") if data.get("folder") in valid_folders else "Inbox",
        "body": str(data.get("body") or original).strip(),
        "tags": [str(t) for t in (data.get("tags") or [])][:6],
        "keywords": [str(k) for k in (data.get("keywords") or [])][:8],
        "due": data.get("due"),
        "remind_at": data.get("remind_at"),
        "tracker": bool(data.get("tracker", False)),
        "reply": str(data.get("reply") or "ذخیره شد ✅").strip(),
    }
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
