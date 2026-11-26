# AI Terminal Bridge

**English:** a self-hosted, open-source bridge that connects AI assistants to Linux servers over SSH through MCP, REST, WebSocket, OpenAI-compatible tool calling and a CLI, with per-server permission levels, human approval, an audit log and a web dashboard (Persian UI). Version 0.1.0: **not independently audited**; read the [security notes](docs/SECURITY.md) before pointing it at a server you care about. Quick start: copy `.env.example` to `.env`, set `ATB_ADMIN_PASSWORD`, run `docker compose up -d --build`, open <http://127.0.0.1:8750>. The detailed documentation is in Persian; the [API reference](docs/API.md) and [tool reference](docs/TOOLS.md) include the English names and schemas. [MIT license](LICENSE).

---

پلی متن‌باز و خودمیزبان که دستیارهای هوش مصنوعی را از راه **MCP، REST، WebSocket، قالب tool calling سازگار با OpenAI و CLI** به ترمینال و فایل‌های سرورهای لینوکسی (از راه SSH) وصل می‌کند؛ با سطح دسترسی، تأیید انسانی، گزارش فعالیت و داشبورد وب فارسی.

وضعیت: نسخه‌ی `0.1.0`. هسته با SSH واقعی تست خودکار شده است، ولی هنوز در محیط عملیاتی استفاده و ممیزی امنیتی مستقل نشده. [آنچه آزمایش شده و نشده](#وضعیت-آزمایش) و [محدودیت‌های امنیتی](docs/SECURITY.md) را پیش از استفاده روی سرور مهم بخوانید.

## شروع سریع

```bash
cp .env.example .env          # Windows: Copy-Item .env.example .env
# ATB_ADMIN_PASSWORD را در .env تنظیم کنید (حداقل ۱۰ نویسه)
docker compose up -d --build
```

> روی Windows با Docker Desktop و تصاویر رسمی Docker Hub ساخته و اجرا شده است (یک ماشین).

1. <http://127.0.0.1:8750> ← ورود با `admin`.
2. «سرورها»: افزودن سرور، گذاشتن کلید عمومی روی آن، تأیید اثر انگشت ([آماده کردن سرور](docs/USAGE.md#آماده-کردن-سرور-مقصد)).
3. «دستیارها»: ساخت توکن و دادن دسترسی.
4. اتصال دستیار، مثلاً Claude Code:

```bash
claude mcp add --transport http terminal-bridge http://127.0.0.1:8750/mcp --header "Authorization: Bearer TOKEN"
```

سپس از دستیار بخواهید: «با ابزار terminal-bridge فهرست سرورها را بگیر و تست‌های پروژه را اجرا کن».

## مستندات

| سند | موضوع |
|---|---|
| [نصب و استفاده](docs/USAGE.md) | Docker، نصب مستقیم، سرور مقصد، پشتیبان‌گیری، رفع اشکال |
| [اتصال دستیارها](docs/CLIENTS.md) | Claude، Gemini، Codex، Cursor، VS Code، Open WebUI، Ollama و ... |
| [امنیت](docs/SECURITY.md) | مدل تهدید و محدودیت‌ها |
| [معماری](docs/ARCHITECTURE.md) | اجزا و تصمیم‌های فنی |
| [سناریوها](docs/SCENARIOS.md) | مثال‌های عملی و تست‌ها |
| [ابزارها](docs/TOOLS.md) · [API](docs/API.md) · [تنظیمات](docs/CONFIG.md) | مرجع (تولیدشده از کد) |

## پوشش مشخصات

✅ ساخته و آزمایش‌شده · 🟡 ناقص یا آزمایش‌نشده (توضیح در همان سطر) · ❌ ساخته نشده

### قابلیت‌ها (بند ۲ و ۴)

| خواسته | وضعیت |
|---|---|
| اتصال SSH، کلید یا گذرواژه، سنجاق کلید میزبان | ✅ کلید در تست خودکار؛ گذرواژه با یک آزمایش جداگانه روی sshd واقعی |
| مقصد Linux | ✅ |
| مقصد Windows / macOS | 🟡 آزمایش نشده؛ دستورها و ابزارهای سیستمی برای Linux نوشته شده‌اند |
| اجرای دستور، خروجی تدریجی، توقف، دستور طولانی | ✅ |
| خواندن، ساخت، ویرایش، حذف، آپلود و دانلود فایل | ✅ (حداکثر ۵ مگابایت) |
| انتقال/تغییر نام فایل | 🟡 ابزار جدا ندارد؛ با `execute_command` (`mv`) |
| مدیریت پروژه | 🟡 با مسیرهای مجاز، فایل‌ها و git؛ موجودیت مستقل «پروژه» ندارد |
| نصب وابستگی | 🟡 با اجرای دستور؛ دسترسی شبکه‌ی sandbox تست شد، نصب واقعی بسته از اینترنت نه |
| اجرای اسکریپت و تست | ✅ با `make`/`unittest`؛ 🟡 مسیرهای `pytest`، `npm`، `go`، `cargo` در `run_tests` جداگانه آزمایش نشده‌اند |
| Git | ✅ محلی (init، status، add، commit، log)؛ 🟡 clone/pull/push با مخزن راه‌دور آزمایش نشده |
| Docker و Compose | ✅ با Docker واقعی و تصویر محلی |
| پردازه‌ها، منابع، لاگ فایل و Docker | ✅ |
| سرویس‌های systemd و لاگ journal | 🟡 پیاده شده، **آزمایش نشده** (محیط توسعه systemd نداشت) |
| نشست تعاملی | ✅ برای انسان (ترمینال وب؛ با tmux ماندگار)؛ ❌ برای دستیار |
| چند سرور هم‌زمان، ادامه پس از قطع اتصال | ✅ |
| snapshot و بازگردانی | ✅ (snapshot تا ۱ گیگابایت) |

### رابط‌ها و داشبورد (بند ۳ و ۵)

| خواسته | وضعیت |
|---|---|
| MCP (HTTP و stdio)، REST، WebSocket، CLI، قالب OpenAI | ✅ |
| Claude Code، Gemini CLI، Codex CLI | 🟡 اتصال واقعی آزمایش شد؛ هیچ مدلی کاری انجام نداد |
| Claude Desktop (Windows) | ✅ چند ابزار با مدل واقعی روی سرور واقعی ([جزئیات](docs/CLIENTS.md))؛ تأیید و بازگردانی از این مسیر آزمایش نشده |
| Cursor، VS Code، Open WebUI، Hermes، Ollama | 🟡 راهنما دارد، آزمایش نشده |
| ChatGPT | ❌ نیازمند OAuth |
| سرورها، دستیارها، دسترسی‌ها، توکن‌ها، تأییدها | ✅ |
| ترمینال زنده، مدیر فایل، ویرایشگر کد، تاریخچه‌ی دستورها، فعالیت زنده | ✅ |
| پایش CPU، RAM، دیسک، شبکه | ✅ |
| تم روشن و تیره، واکنش‌گرا | ✅ |
| دیدن تغییر فایل پیش از اعمال | 🟡 برای عملیات نیازمند تأیید و ویرایش خودتان؛ در سطح‌های بدون تأیید تغییر فوراً اعمال می‌شود (قابل بازگردانی) |
| فهرست اتصال‌های SSH و نشست‌ها | 🟡 صفحه‌ی مستقل ندارد (دستورها و آزمایش اتصال دارد) |
| تنظیمات امنیتی | 🟡 گذرواژه، کاربران، توقف اضطراری؛ محدودیت نرخ و `deny_globs` فقط از API |

### دسترسی و امنیت (بند ۶ و ۷)

| خواسته | وضعیت |
|---|---|
| پنج سطح دسترسی برای هر دستیار و سرور | ✅ |
| دسترسی جدا برای هر پروژه | 🟡 با قواعد مسیر (rw/ro)؛ برای سطح متفاوت روی یک سرور دستیار دوم بسازید |
| دسترسی جدا برای هر کاربر و RBAC | 🟡 دو نقش `admin` و `user`؛ هر کاربر فقط منابع خود را می‌بیند؛ نقش سفارشی ندارد |
| رمزنگاری اسرار، SSH Key، هش گذرواژه و توکن | ✅ |
| ارتباط رمزشده | ✅ SSH؛ HTTPS با Caddy (آزمایش محلی با بسته‌ی Ubuntu، نه دامنه‌ی عمومی) |
| ورود امن | ✅ گذرواژه و قفل موقت؛ ❌ ورود دومرحله‌ای و OAuth |
| sandbox | ✅ bubblewrap برای `developer` و `force_sandbox`؛ ❌ اجرای ایزوله با Docker |
| محدودیت دستور و مسیر، Command Injection | ✅ (فیلتر دستور مرز امنیتی نیست) |
| Prompt Injection | 🟡 فقط کاهش اثر با دسترسی و تأیید؛ راه‌حل کامل ندارد |
| پوشاندن اسرار | 🟡 بر اساس الگو؛ قابل دور زدن |
| محدودیت منابع و زمان | 🟡 زمان، خروجی، هم‌زمانی، نرخ؛ حافظه اختیاری؛ ❌ CPU و دیسک |
| Audit Log، تأیید، توقف فوری، بازگردانی | ✅ |
| بدون نیاز به root | ✅ (اگر خودتان کاربر root اضافه کنید جلوگیری نمی‌شود؛ `connect_server` آن را گزارش می‌کند) |

### فنی (بند ۹)

ماژولار و async ✅ · خودمیزبان و بدون سرویس پولی ✅ · چند کاربر و چند دستیار ✅ · افزونه ✅ · مستندات API ✅ · Docker Compose ✅ · VPS کوچک 🟡 (مصرف اندازه‌گیری شد، روی VPS واقعی اجرا نشد)

## وضعیت آزمایش

محیط توسعه: Linux (Ubuntu)، Python 3.13، Node 22، OpenSSH و bubblewrap واقعی.

- **تست خودکار:** ۱۴۰ تست روی SQLite و نیز روی PostgreSQL 16 (شامل ۷ تست Docker)؛ `ruff` بدون خطا.
- **داشبورد:** یک سناریوی کامل در مرورگر واقعی (Chrome) از ورود تا تأیید، ویرایش فایل، ترمینال، توقف اضطراری و نمای موبایل.
- **Docker Compose:** در محیط توسعه (با تصاویر پایه‌ی جایگزین): ساخت، اجرا، سلامت، راه‌اندازی مجدد هنگام اجرای دستور، کاربر غیر root و فایل‌سیستم فقط‌خواندنی. روی Windows با Docker Desktop و تصاویر رسمی: ساخت، اجرا، سلامت، ورود و افزودن یک سرور لینوکسی واقعی.
- **مدل واقعی:** Claude از راه Claude Desktop روی Windows فهرست سرورها، اتصال، ساخت پوشه، نوشتن فایل و اجرای آن در sandbox را انجام داد.
- **کلاینت‌ها:** Claude Code 2.1.332، Gemini CLI 0.71.1، Codex CLI 0.172.0 (اتصال، بدون مدل).
- **آزمایش نشده:** تأیید عملیات و بازگردانی با مدل واقعی، سایر کلاینت‌های جدول، systemd، دامنه‌ی عمومی، پروفایل‌های `https` و `postgres` در Compose، نصب مستقیم روی Windows (بدون Docker) و macOS.
- **CI گیت‌هاب:** فقط `ruff`، تست‌های واحد و مستندات و ساخت داشبورد؛ تست‌های SSH را روی Linux خودتان اجرا کنید.

این تست‌ها نشان می‌دهند کنترل‌ها در سناریوهای آزموده‌شده کار می‌کنند؛ جای آزمون نفوذ مستقل را نمی‌گیرند.

## کارایی

روی ۲ هسته و ۷ گیگابایت حافظه؛ پل، sshd و کلاینت روی **یک ماشین**؛ ۱۰۰ درخواست در هر ردیف. روی شبکه‌ی واقعی تأخیر بیشتر است.

حافظه‌ی پل: حدود ۱۱۷ مگابایت در بیکاری، اوج حدود ۱۸۰.

| هم‌زمانی | `read_file` میانه/p95 (ms)، req/s | `list_directory` | `execute_command` |
|---|---|---|---|
| ۱ | 31/45 ، 31 | 25/34 ، 38 | 129/150 ، 8.1 |
| ۴ | 96/122 ، 41 | 81/116 ، 48 | 223/304 ، 17 |
| ۱۶ | 338/467 ، 47 | 312/403 ، 50 | 760/1125 ، 19 |

تکرار: `python backend/scripts/benchmark.py --help`

## ساختار

```
backend/    پل (Python/FastAPI)، تست‌ها، اسکریپت‌ها
frontend/   داشبورد (React/TypeScript)
docs/       مستندات
examples/   عامل سازگار با OpenAI و نمونه‌ی افزونه
deploy/     پیکربندی Caddy
```

## توسعه

```bash
pip install -e "./backend[dev]"
cd frontend && npm ci && npm run dev      # داشبورد با پروکسی به پل روی 8750
python backend/scripts/gen_docs.py        # پس از تغییر ابزارها، API یا تنظیمات
```

## مجوز

[MIT](LICENSE). مجوز کتابخانه‌ها و قلم Vazirmatn (OFL) که در داشبورد بسته‌بندی شده‌اند در [third-party-licenses.txt](frontend/public/third-party-licenses.txt) آمده است.
