"""سینک والت با گیت‌هاب — برای وقتی که ربات روی هاست ابری اجراست.

ایده: ربات روی هاست رایگان اجرا می‌شود و بعد از هر ذخیره،
نوت‌ها را کامیت و پوش می‌کند. روی لپ‌تاپ هم با پلاگین Obsidian Git
هر چند دقیقه پول می‌گیری و نوت‌ها خودکار می‌آیند.
اگر GIT_SYNC_ENABLED=false باشد همه توابع بی‌اثرند و ربات
مثل قبل فقط محلی کار می‌کند.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from config import settings

log = logging.getLogger(__name__)


def _run_git(args: list[str], cwd: Path, timeout: int = 30) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        ok = proc.returncode == 0
        out = (proc.stdout + proc.stderr).strip()[-500:]
        return ok, out
    except FileNotFoundError:
        log.warning("git نصب نیست — سینک غیرفعال شد.")
        return False, "git not installed"
    except Exception as exc:
        log.warning("git error: %s", exc)
        return False, str(exc)


def ensure_repo(root: Path) -> bool:
    """اگر والت هنوز ریپو نیست، init کن و به ریموت وصل شو. اول اجرا صدا بزن."""
    if not settings.git_sync or not settings.git_repo_url:
        return False
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        ok, out = _run_git(["init", "-b", settings.git_branch], root)
        if not ok:
            log.warning("git init failed: %s", out)
            return False
        _run_git(["remote", "add", "origin", settings.git_repo_url], root)
        # اگر ریپوی ریموت فایل دارد، اول پول بگیر تا تاریخچه یکی شود
        _run_git(["pull", "--rebase", "origin", settings.git_branch], root)
        _run_git(["config", "user.name", "obsidian-bot"], root)
        _run_git(["config", "user.email", "obsidian-bot@localhost"], root)
        log.info("Git repo initialized in %s", root)
    else:
        # هر بار که ربات بالا می‌آید، آخرین تغییرات لپ‌تاپ را بگیر
        _run_git(["pull", "--rebase", "--autostash", "origin", settings.git_branch], root)
    return True


def sync_changes(root: Path, message: str = "bot: update notes") -> bool:
    """add + commit + push. خطا هرگز ربات را نمی‌خواباند."""
    if not settings.git_sync or not settings.git_repo_url:
        return False
    if not (root / ".git").exists():
        if not ensure_repo(root):
            return False
    ok, _ = _run_git(["add", "-A"], root)
    if not ok:
        return False
    # اگر چیزی عوض نشده، کامیت خالی نزن
    ok, out = _run_git(["status", "--porcelain"], root)
    if not ok or not out.strip():
        return True
    _run_git(["-c", "user.name=obsidian-bot",
              "-c", "user.email=obsidian-bot@localhost",
              "commit", "-m", message], root)
    ok, out = _run_git(["push", "-u", "origin", settings.git_branch], root)
    if not ok:
        log.warning("git push failed: %s", out)
        return False
    log.info("Vault pushed: %s", message)
    return True
