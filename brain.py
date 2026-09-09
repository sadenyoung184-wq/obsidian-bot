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


def _system_text() -> str:
    return SYSTEM_PROMPT.format(
        now=datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="minutes"),
        tz=settings.timezone,
    )


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


def analyze(text: str) -> dict:
    """پیام کاربر را تحلیل کن و دیکشنری استاندارد برگردان."""
    fallback = _fallback(text)
    if not settings.gemini_api_key or genai is None:
        return fallback
    try:
        resp = _get_client().models.generate_content(
            model=settings.gemini_model,
            contents=text,
            config=genai_types.GenerateContentConfig(
                system_instruction=_system_text(),
                response_mime_type="application/json",
            ),
        )
        data = json.loads(_strip_fences(resp.text or ""))
        return _normalize(data, text)
    except Exception as exc:  # قطع بودن API نباید ربات را بخواباند
        log.warning("Gemini failed, using fallback: %s", exc)
        return fallback


def ask(question: str) -> str:
    """گفت‌وگوی آزاد (برای type=question هم استفاده می‌شود)."""
    if genai is None or not settings.gemini_api_key:
        return "الان به هوش مصنوعی دسترسی ندارم، ولی پیامت را در Inbox ذخیره کردم. بعداً دوباره بپرس."
    try:
        resp = _get_client().models.generate_content(
            model=settings.gemini_model,
            contents="به این سوال به فارسی، کوتاه و کاربردی جواب بده:\n" + question,
            config=genai_types.GenerateContentConfig(
                system_instruction=_system_text(),
            ),
        )
        return (resp.text or "").strip()
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
