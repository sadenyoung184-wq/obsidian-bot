"""حالت پیش‌نویس: هیچ پیامی مستقیم ذخیره نمی‌شود.

جریان کار:
1. هر پیام عادی → تحلیل با Gemini → ذخیره در Draft (نه در والت)
2. کاربر نظرش را در پیام‌های بعدی می‌گوید («عنوان را عوض کن»، «خوبه ولی ...»)
3. با /done یا «تایید» → همه پیش‌نویس‌ها یکجا پردازش و در والت ذخیره می‌شوند
4. با /cancel → همه دور ریخته می‌شوند

پیش‌نویس‌ها در drafts.json کنار کد ذخیره‌اند تا با ری‌استارت هاست نپرند.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

STORE = Path(__file__).parent / "drafts.json"

# حالت‌های گفت‌وگو برای هر چت
MODE_AUTO = "auto"      # رفتار قدیمی: ذخیره فوری (با /auto فعال می‌شود)
MODE_DRAFT = "draft"    # پیش‌فرض جدید: جمع کردن + تایید

CONFIRM_WORDS = ("تایید", "تاييد", "تأیید", "اوکی", "باشه", "انجام بده",
                 "ذخیره کن", "ثبت کن", "done", "/done", "confirm", "ok")


def _load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"modes": {}, "drafts": {}}


def _save(state: dict) -> None:
    try:
        STORE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("Could not persist drafts")


class DraftStore:
    def get_mode(self, chat_id: int) -> str:
        return _load().get("modes", {}).get(str(chat_id), MODE_DRAFT)

    def set_mode(self, chat_id: int, mode: str) -> None:
        state = _load()
        state.setdefault("modes", {})[str(chat_id)] = mode
        _save(state)

    def add(self, chat_id: int, raw_text: str, result: dict) -> int:
        """پیش‌نویس جدید اضافه کن؛ شماره‌اش را برگردان."""
        state = _load()
        key = str(chat_id)
        state.setdefault("drafts", {}).setdefault(key, [])
        state["drafts"][key].append({"raw": raw_text, "result": result})
        _save(state)
        return len(state["drafts"][key])

    def list(self, chat_id: int) -> list[dict]:
        return _load().get("drafts", {}).get(str(chat_id), [])

    def update_last(self, chat_id: int, result: dict) -> bool:
        state = _load()
        items = state.get("drafts", {}).get(str(chat_id), [])
        if not items:
            return False
        items[-1]["result"] = result
        _save(state)
        return True

    def clear(self, chat_id: int) -> int:
        state = _load()
        items = state.get("drafts", {}).pop(str(chat_id), [])
        _save(state)
        return len(items)

    @staticmethod
    def is_confirm(text: str) -> bool:
        t = (text or "").strip().lower()
        if t in ("done", "confirm", "ok", "تایید", "تاييد", "تأیید"):
            return True
        return any(w in (text or "") for w in CONFIRM_WORDS)
