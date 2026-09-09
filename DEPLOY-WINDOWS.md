# راهنمای قدم‌به‌قدم — ویندوز + هاست ابری رایگان

وضعیت الآن تو:
- [x] والت `E:\my-vault` ساخته شد (۱۰ پوشه + Todo + نوت خوش‌آمد)
- [x] گیت داخل والت init شد و اولین کامیت ثبت شد
- [x] پروژه ربات آماده هاست ابری است (`render.yaml` + `git_sync.py`)

فقط این ۴ قدم مانده — همه‌شان بیرون از لپ‌تاپ‌اند و من به آن‌ها دسترسی ندارم،
ولی هر قدم ۲ تا ۵ دقیقه است:

## قدم ۱ — والت را روی گیت‌هاب بگذار (۵ دقیقه)

۱. به https://github.com/new برو → اسم `my-vault` → گزینه **Private** → Create.
۲. روی لپ‌تاپ، PowerShell را باز کن و این دستورها را بزن
(به‌جای `USERNAME` یوزرنیم گیت‌هابت را بگذار):

```powershell
cd E:\my-vault
git remote add origin https://github.com/USERNAME/my-vault.git
git push -u origin main
```

اگر یوزر/پسورد خواست: پسورد همان توکن قدم ۲ است.

## قدم ۲ — توکن گیت‌هاب بساز (۲ دقیقه)

۱. برو به https://github.com/settings/tokens/new
۲. تیک `repo` را بزن → Generate → توکن را کپی کن (فقط یک‌بار نشان داده می‌شود).
۳. آدرس سینک را این‌طور بساز و نگه دار:

```
https://USERNAME:TOKEN@github.com/USERNAME/my-vault.git
```

## قدم ۳ — پروژه ربات را روی گیت‌هاب بگذار (۳ دقیقه)

۱. یک ریپوی **Public** به اسم `obsidian-bot` بساز.
۲. فایل `obsidian-assistant-bot.zip` (همین‌جا در پوشه خروجی) را باز کن
و همه فایل‌های داخلش را با GitHub Desktop یا دستورهای زیر پوش کن:

```powershell
# پوشه ربات را جایی باز کن، بعد:
git init -b main
git add -A
git commit -m "obsidian assistant bot"
git remote add origin https://github.com/USERNAME/obsidian-bot.git
git push -u origin main
```

## قدم ۴ — روشن کردن ربات در Render (۵ دقیقه، رایگان)

۱. برو به https://render.com → Sign up با گیت‌هاب.
۲. New → **Blueprint** → ریپوی `obsidian-bot` را انتخاب کن → Apply.
۳. مقادیر خواسته‌شده را وارد کن:

| کلید | مقدار |
|---|---|
| `TELEGRAM_TOKEN` | توکنی که از BotFather گرفتی |
| `GEMINI_API_KEY` | کلیدی که از aistudio گرفتی |
| `ALLOWED_USER_IDS` | آیدی عددی‌ات از userinfobot |
| `GIT_REPO_URL` | آدرس ساخته‌شده در قدم ۲ |

۴. Deploy که سبز شد، در تلگرام به رباتت `/start` بده. جواب داد = تمام شد ✅

## قدم ۵ — سینک خودکار در Obsidian (۳ دقیقه)

۱. در Obsidian → Settings → Community plugins → **Obsidian Git** را نصب و Enable کن.
۲. والت `E:\my-vault` را در Obsidian باز کن (Open folder as vault).
۳. در تنظیمات Obsidian Git:
- Pull: هر `10` دقیقه
- Push after commit: روشن
۴. از این به بعد نوت‌های ربات خودکار می‌آیند و ویرایش‌های تو هم بالا می‌رود.

## اگر جایی گیر کردی

- Render لاگ زنده دارد: Dashboard → سرویس → Logs. خطا را برایم بفرست تا درستش کنم.
- اگر خواستی اول روی لپ‌تاپ تست کنی: فایل `.env` را از روی `.env.example` بساز،
مقادیر را بگذار، `VAULT_PATH=E:/my-vault` و `pip install -r requirements.txt` و `python bot.py`.
