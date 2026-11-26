# نصب و استفاده

## پیش‌نیازها

- **ماشین پل:** Docker با Compose؛ یا Python 3.11+ و (برای ساخت داشبورد) Node.js 22.12+.
- **سرور مقصد:** لینوکس با SSH و SFTP، `bash`، `coreutils`، `util-linux` (برای `setsid`)، `procps`، `grep`، `find`، `tar`. توصیه‌شده: `bubblewrap` (sandbox)، `tmux` (ماندگاری ترمینال)، `git`. مقصد Windows یا macOS آزمایش نشده و ابزارهای سیستمی روی آن‌ها کار نمی‌کنند یا تضمینی ندارند.

## نصب با Docker Compose

Linux:

```bash
cp .env.example .env
# در .env مقدار ATB_ADMIN_PASSWORD (حداقل ۱۰ نویسه) را بگذارید
docker compose up -d --build
```

Windows (PowerShell، با Docker Desktop):

```powershell
cd C:\path\to\ai-terminal-bridge
Copy-Item .env.example .env
notepad .env          # ATB_ADMIN_PASSWORD را تنظیم کنید
docker compose up -d --build
```

سپس <http://127.0.0.1:8750> را باز کنید و با `admin` وارد شوید.

> ساخت با تصاویر رسمی Docker Hub (`node:22-slim`، `python:3.13-slim`) و اجرا روی Windows با Docker Desktop روی یک ماشین انجام شد. Docker Desktop باید پیش از `docker compose up` در حال اجرا باشد (`docker desktop start`).

اگر شبکه‌ی شما به Docker Hub دسترسی ندارد، تصاویر پایه را از رجیستری دیگری که به آن اعتماد دارید با `NODE_IMAGE` و `PYTHON_IMAGE` (و برای پروفایل‌ها `CADDY_IMAGE` و `POSTGRES_IMAGE`) در `.env` تعیین کنید.

اگر می‌خواهید همان ماشینِ میزبان Docker را (لینوکس) به‌عنوان سرور اضافه کنید، نشانی آن `host.docker.internal` است و SSH باید روی آن در دسترس باشد.

### دسترسی از راه دور با HTTPS

DNS دامنه را به سرور وصل کنید، درگاه‌های ۸۰ و ۴۴۳ را باز کنید، در `.env` مقدار `ATB_DOMAIN=bridge.example.com` بگذارید:

```bash
docker compose --profile https up -d --build
```

Caddy گواهی را خودکار می‌گیرد. اگر عمومی نمی‌خواهید، پیش‌فرض `127.0.0.1` را نگه دارید و از تونل SSH یا VPN استفاده کنید: `ssh -L 8750:127.0.0.1:8750 user@bridge-host`.

### PostgreSQL

در `.env` (نویسه‌های ویژه‌ی گذرواژه در URL باید encode شوند):

```ini
POSTGRES_PASSWORD=یک-گذرواژه
ATB_DATABASE_URL=postgresql+asyncpg://atb:یک-گذرواژه@postgres:5432/atb
```

```bash
docker compose --profile postgres up -d --build
```

پل تا آماده شدن PostgreSQL ممکن است چند بار راه‌اندازی مجدد شود. انتقال داده از SQLite خودکار نیست.

## نصب مستقیم (بدون Docker)

Linux:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install ./backend
(cd frontend && npm ci && npm run build)
export ATB_ADMIN_PASSWORD='گذرواژه‌ی-حداقل-ده-نویسه' ATB_STATIC_DIR="$PWD/frontend/dist"
atb serve
```

Windows PowerShell (**آزمایش‌نشده**):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install .\backend
cd frontend; npm ci; npm run build; cd ..
$env:ATB_ADMIN_PASSWORD = "گذرواژه‌ی-حداقل-ده-نویسه"
$env:ATB_STATIC_DIR = "$PWD\frontend\dist"
atb serve
```

داده‌ها در `ATB_DATA_DIR` (پیش‌فرض `data` در پوشه‌ی اجرا) ذخیره می‌شوند. روی Windows مجوز فایل `master.key` مثل لینوکس محدود نمی‌شود؛ خودتان دسترسی پوشه را محدود کنید. همه‌ی تنظیمات: [CONFIG.md](CONFIG.md).

## آماده کردن سرور مقصد

روی سرور (Ubuntu/Debian):

```bash
sudo apt install bubblewrap tmux git
sudo useradd --create-home --shell /bin/bash atb
sudo usermod -p '*' atb                      # بدون ورود با گذرواژه، بدون قفل شدن حساب
sudo install -d -o atb -g atb /srv/projects  # پروژه‌ها بیرون از home
```

بررسی کار کردن sandbox:

```bash
sudo -u atb bwrap --unshare-pid --unshare-net --ro-bind / / --proc /proc true && echo OK
```

**Ubuntu 23.10 به بعد** ممکن است user namespace بدون امتیاز را با AppArmor محدود کند ([توضیح رسمی](https://ubuntu.com/blog/ubuntu-23-10-restricted-unprivileged-user-namespaces)) و دستور بالا خطا دهد. یک راه گزارش‌شده برای 24.04 ([منبع](https://github.com/microsoft/vscode/issues/316046)؛ اینجا آزمایش نشده):

```bash
sudo apt install apparmor-profiles
sudo cp /usr/share/apparmor/extra-profiles/bwrap-userns-restrict /etc/apparmor.d/
sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
```

اگر sandbox کار نکند: دستورهای `developer` تأیید انسان می‌خواهند و دسترسی‌های دارای `force_sandbox` دستور اجرا نمی‌کنند.

### افزودن سرور

1. داشبورد ← «سرورها» ← نام، میزبان، درگاه، کاربر `atb`، احراز هویت «ساخت کلید».
2. کلید عمومی نمایش‌داده‌شده را روی سرور اضافه کنید:

   ```bash
   sudo install -d -m 700 -o atb -g atb /home/atb/.ssh
   echo 'ssh-ed25519 AAAA...' | sudo tee -a /home/atb/.ssh/authorized_keys
   sudo chown atb:atb /home/atb/.ssh/authorized_keys && sudo chmod 600 /home/atb/.ssh/authorized_keys
   ```

3. «دریافت اثر انگشت» ← آن را با خروجی `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` **روی خود سرور** مقایسه و تأیید کنید.
4. اتصال را آزمایش کنید.

## دسترسی دستیارها

داشبورد ← «دستیارها» ← ساخت توکن ← انتخاب سرور ← «اعطای دسترسی». دسترسی برای هر زوج (دستیار، سرور) جداست و مسیرها نقش محدوده‌ی «پروژه» را دارند.

| سطح | رفتار |
|---|---|
| `read_only` | فقط خواندن فایل‌ها و اطلاعات سیستم |
| `developer` | نوشتن در مسیرهای مجاز و اجرای کد در sandbox |
| `operator` | اجرای دستور و مدیریت سرویس‌های مجاز (بدون sandbox مگر `force_sandbox`) |
| `approval_required` | هر تغییری منتظر تأیید می‌ماند |
| `autonomous` | بدون تأیید در محدوده‌ی دسترسی؛ عملیات مخرب فقط با `auto_approve_destructive` |

جدول دقیق تصمیم‌ها: [TOOLS.md](TOOLS.md#ماتریس-تصمیم). اتصال هر کلاینت: [CLIENTS.md](CLIENTS.md).

### نکته‌های کاربردی

- **نصب وابستگی در sandbox:** شبکه پیش‌فرض خاموش است؛ برای `pip`/`npm`/`git pull`/`git push` گزینه‌ی `sandbox_network` را روشن کنید.
- **ابزارهای نصب‌شده در home** (مثل nvm، pyenv، `~/.local`) در sandbox دیده نمی‌شوند (`HOME=/tmp/home` و `PATH` سیستمی). ابزار را سیستمی نصب کنید یا محیط مجازی را داخل پوشه‌ی پروژه بسازید.
- **مسیر نوشتنی** نباید شامل home کاربر SSH یا symlink باشد.
- `git clone/push` با SSH در sandbox به کلید دسترسی ندارد (`~/.ssh` پنهان است)؛ از HTTPS با credential محدود استفاده کنید یا این کار را خودتان انجام دهید.
- در سطح‌های بدون sandbox، `git commit` هویت git تنظیم‌شده روی سرور را لازم دارد.
- «فهرست دستورهای مجاز» روی `execute_command`، `run_tests` و عملیات نوشتنی git هم اعمال می‌شود؛ برنامه‌ی لازم (`git`، `python3`، `make`، ...) را در فهرست بگذارید.
- `max_memory_mb` را برای پروژه‌های Node صفر بگذارید: در آزمایش، با سقف ۲ تا ۸ گیگابایت `fetch` در Node 22 با خطای حافظه شکست خورد.

## کار روزمره

- **تأییدها:** صفحه‌ی «تأییدها»؛ پیش‌نمایش را کامل بخوانید.
- **تغییرات فایل و snapshot:** صفحه‌ی «فایل‌ها»، بخش تاریخچه، برای بازگردانی.
- **توقف اضطراری** (مدیر): دکمه‌ی بالای داشبورد؛ دستورهای دستیارها متوقف و فراخوانی‌ها رد می‌شوند.
- **کاربران:** صفحه‌ی «تنظیمات»، یا:

  ```bash
  docker compose exec bridge atb create-user NAME --admin
  ```

  (فراموشی گذرواژه‌ی مدیر هم با ساخت یک مدیر دیگر حل می‌شود.)
- **بررسی Audit:** `docker compose exec bridge atb verify-audit`

## پشتیبان‌گیری و ارتقا

`master.key` (یا `ATB_MASTER_KEY`) بدون پشتیبان یعنی از دست رفتن کلیدهای SSH ذخیره‌شده. پشتیبان‌گیری سازگار از نسخه‌ی SQLite:

```bash
docker compose stop bridge
docker compose cp bridge:/data ./atb-backup
docker compose start bridge
```

پوشه‌ی پشتیبان شامل کلید اصلی و پایگاه‌داده است؛ آن را امن نگه دارید و کلید را ترجیحاً جدا بایگانی کنید. ارتقا: فایل‌های جدید را جایگزین و `docker compose up -d --build` را اجرا کنید. این نسخه مهاجرت خودکار پایگاه‌داده ندارد.

## رفع اشکال

| پیام | راه‌حل |
|---|---|
| `HOST KEY MISMATCH` | کلید سرور عوض شده؛ علت را بررسی و سپس دوباره اثر انگشت را تأیید کنید |
| `contains the SSH user's home directory` | به‌جای home یک پوشه‌ی پروژه بدهید |
| `is or contains a symbolic link` | مسیر واقعی را در دسترسی بنویسید |
| `bubblewrap does not work on the server` | بخش «آماده کردن سرور مقصد» را ببینید |
| `only these programs are allowed` | فهرست دستورهای مجاز را اصلاح کنید |
| `passphrase-protected keys are not supported` | کلید جدا و بدون passphrase برای پل بسازید |
| داشبورد باز نمی‌شود (نصب مستقیم) | `ATB_STATIC_DIR` باید به `frontend/dist` ساخته‌شده اشاره کند |
| `no users exist yet` | `ATB_ADMIN_PASSWORD` را تنظیم کنید یا `atb create-user NAME --admin` |

لاگ: `docker compose logs -f bridge`.
