"""تنظیمات مرکزی ربات — همه‌چیز از فایل .env خوانده می‌شود."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


@dataclass
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    allowed_user_ids: set[int] = field(
        default_factory=lambda: _parse_ids(os.getenv("ALLOWED_USER_IDS", ""))
    )
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
    vault_path: str = os.getenv("VAULT_PATH", "./MyVault")
    timezone: str = os.getenv("TIMEZONE", "Asia/Tehran")
    # --- سینک گیت (برای هاست ابری: نوت‌ها به ریپوی گیت‌هاب پوش می‌شوند) ---
    git_sync: bool = os.getenv("GIT_SYNC_ENABLED", "false").lower() in ("1", "true", "yes")
    git_repo_url: str = os.getenv("GIT_REPO_URL", "")
    git_branch: str = os.getenv("GIT_BRANCH", "main")
    # --- گزارش خودکار ---
    digest_chat_id: str = os.getenv("DIGEST_CHAT_ID", "")  # آیدی عددی برای گزارش صبحگاهی
    digest_time: str = os.getenv("DIGEST_TIME", "07:30")  # ساعت ارسال (HH:MM)
    weekly_digest: bool = os.getenv("WEEKLY_DIGEST", "true").lower() in ("1", "true", "yes")

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.telegram_token:
            problems.append("TELEGRAM_TOKEN تنظیم نشده (فایل .env را ببین).")
        if not self.gemini_api_key:
            problems.append("GEMINI_API_KEY تنظیم نشده (از aistudio.google.com بگیر).")
        return problems


settings = Settings()
