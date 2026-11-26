# اتصال دستیارها

ابتدا در داشبورد: «دستیارها» ← ساخت توکن ← «اعطای دسترسی» روی سرور. توکن فقط یک بار نمایش داده می‌شود. برای هر دستیار توکن جدا بسازید.

نمونه‌ها نشانی `http://127.0.0.1:8750` را فرض می‌کنند.

## وضعیت سازگاری

| کلاینت | روش | وضعیت |
|---|---|---|
| Claude Code (نسخه 2.1.332) | HTTP و stdio | ✅ اتصال و دست‌دهی آزمایش شد؛ مدل فراخوانی نشد |
| Gemini CLI (نسخه 0.71.1) | HTTP و stdio | ✅ اتصال و دست‌دهی آزمایش شد؛ مدل فراخوانی نشد |
| Codex CLI (نسخه 0.172.0) | HTTP و stdio | ✅ اتصال و فهرست ۲۵ ابزار آزمایش شد؛ مدل فراخوانی نشد |
| MCP Python SDK، REST، WebSocket، قالب OpenAI، CLI | — | ✅ فراخوانی ابزار در تست خودکار (مدلِ اسکریپتی، نه مدل واقعی) |
| Claude Desktop (Windows) | stdio محلی | ✅ روی یک ماشین با بسته‌ی MSIX نسخه‌ی 6.28899.6: چند ابزار با مدل واقعی اجرا شد (فهرست در بخش Claude Desktop) |
| Claude (وب و موبایل، connector سفارشی) | HTTPS عمومی | ⚠️ آزمایش نشده؛ محدودیت‌ها را پایین ببینید |
| Cursor، VS Code، Open WebUI، Hermes Agent | HTTP | ⚠️ آزمایش نشده؛ بر پایه‌ی مستندات رسمی |
| Ollama و مدل‌های محلی | `examples/openai_agent.py` | ⚠️ اسکریپت با مدل ساختگی تست شد؛ با Ollama واقعی نه |
| ChatGPT (connector) | — | ❌ پشتیبانی نمی‌شود |

## Claude Code

```bash
claude mcp add --transport http terminal-bridge http://127.0.0.1:8750/mcp \
  --header "Authorization: Bearer TOKEN"
# یا stdio:
claude mcp add terminal-bridge -e ATB_URL=http://127.0.0.1:8750 -e ATB_AGENT_TOKEN=TOKEN -- atb mcp-stdio
claude mcp list
```

## Gemini CLI

```bash
gemini mcp add --scope user --transport http --header "Authorization: Bearer TOKEN" terminal-bridge http://127.0.0.1:8750/mcp
# یا stdio:
gemini mcp add --scope user -e ATB_URL=http://127.0.0.1:8750 -e ATB_AGENT_TOKEN=TOKEN terminal-bridge atb mcp-stdio
```

در آزمایش، Gemini CLI فقط پس از «مورد اعتماد» کردن پوشه‌ی جاری سرور را وصل نشان داد.

## Codex CLI

```bash
export ATB_AGENT_TOKEN=TOKEN
codex mcp add terminal-bridge --url http://127.0.0.1:8750/mcp --bearer-token-env-var ATB_AGENT_TOKEN
# یا stdio:
codex mcp add terminal-bridge --env ATB_URL=http://127.0.0.1:8750 --env ATB_AGENT_TOKEN=TOKEN -- atb mcp-stdio
```

## Claude Desktop (محلی، stdio)

بخش `mcpServers` زیر را در `claude_desktop_config.json` بگذارید. برنامه را **پیش از ویرایش** کامل ببندید (Quit از آیکن کنار ساعت یا نوار منو) و بعد دوباره باز کنید. اگر فایل تنظیمات دیگری هم دارد، فقط کلید `mcpServers` را اضافه کنید و بقیه را پاک نکنید. مراقب باشید دور توکن فاصله‌ی اضافه نماند (خطای `Illegal header value`).

**محل فایل در Windows** معمولاً `%APPDATA%\Claude\` است، ولی روی نصب‌های بسته‌بندی‌شده (MSIX) ممکن است جای دیگری باشد. روی ماشینی که آزمایش شد، برنامه این فایل را از `%LOCALAPPDATA%\Claude-Data\<نام بسته>\` می‌خواند (مشاهده روی یک ماشین و یک نسخه). برای پیدا کردن پوشه‌ی داده، در حالی که برنامه باز است:

```powershell
Get-CimInstance Win32_Process -Filter "Name='Claude.exe'" |
  ForEach-Object { if ($_.CommandLine -match '--user-data-dir=("[^"]+"|[^"]+?(?="|\s+(--|/)|$))') { $Matches[1].Trim('"') } } |
  Sort-Object -Unique
```

آزمایش‌شده با روش Docker زیر: اتصال از گفتگویی که به همان کامپیوتر وصل بود و ابزارهای `list_servers`، `connect_server`، `create_directory`، `write_file` و `execute_command` (در sandbox) روی یک سرور لینوکسی واقعی. تأیید عملیات و بازگردانی از این مسیر آزمایش نشده است.

**اگر پل با Docker Compose اجرا می‌شود** (روی میزبان به Python نیاز نیست). مسیر فایل Compose را با مسیر خودتان عوض کنید. اگر برنامه `docker` را پیدا نکرد، مسیر کامل آن را در `command` بگذارید (معمولاً `C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe`):

```json
{
  "mcpServers": {
    "terminal-bridge": {
      "command": "docker",
      "args": ["compose", "-f", "C:\\path\\to\\ai-terminal-bridge\\docker-compose.yml", "exec", "-T",
               "-e", "ATB_AGENT_TOKEN", "bridge", "atb", "mcp-stdio"],
      "env": { "ATB_AGENT_TOKEN": "TOKEN" }
    }
  }
}
```

**اگر `atb` روی همان ماشین نصب است** (`pip install ./backend`؛ این روش با Claude Desktop آزمایش نشده):

```json
{
  "mcpServers": {
    "terminal-bridge": {
      "command": "atb",
      "args": ["mcp-stdio"],
      "env": { "ATB_URL": "http://127.0.0.1:8750", "ATB_AGENT_TOKEN": "TOKEN" }
    }
  }
}
```

اگر Claude Desktop برنامه را پیدا نکرد، مسیر کامل `atb.exe` را در `command` بگذارید.

## Claude در وب و موبایل (connector سفارشی) — آزمایش نشده

- سرور باید با **HTTPS عمومی** از زیرساخت Anthropic در دسترس باشد (نه localhost یا VPN) ([راهنمای پشتیبانی](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)).
- احراز هویت با توکن ثابت در «Request headers» انجام می‌شود، ولی مستندات توسعه‌دهندگان آن را قابلیت بتا برای سازمان‌های محدود توصیف می‌کنند ([remote MCP](https://claude.com/docs/connectors/custom/remote-mcp)، [authentication](https://claude.com/docs/connectors/building/authentication)). اگر این گزینه را ندارید، وصل نمی‌شود چون پل OAuth ندارد.
- عمومی کردن پلی که به ترمینال سرور راه دارد ریسک جدی است؛ ترجیحاً از Claude Code یا Desktop استفاده کنید.

## ChatGPT — پشتیبانی نمی‌شود

بر پایه‌ی [مستندات OpenAI](https://developers.openai.com/plugins/build/auth)، ChatGPT برای سرور MCP یا OAuth 2.1 می‌خواهد یا بدون احراز هویت، و توکن ثابت نمی‌فرستد. پل OAuth ندارد (و بدون احراز هویت اجرا نمی‌شود). از Codex CLI استفاده کنید، یا مدل‌های OpenAI را از راه `examples/openai_agent.py` وصل کنید.

## Cursor — آزمایش نشده

`~/.cursor/mcp.json` یا `.cursor/mcp.json` ([مستندات](https://cursor.com/docs/mcp)):

```json
{ "mcpServers": { "terminal-bridge": {
  "url": "http://127.0.0.1:8750/mcp",
  "headers": { "Authorization": "Bearer ${env:ATB_AGENT_TOKEN}" } } } }
```

## VS Code — آزمایش نشده

`.vscode/mcp.json` ([مستندات](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)):

```json
{
  "inputs": [ { "type": "promptString", "id": "atb-token", "description": "Terminal bridge token", "password": true } ],
  "servers": { "terminal-bridge": {
    "type": "http", "url": "http://127.0.0.1:8750/mcp",
    "headers": { "Authorization": "Bearer ${input:atb-token}" } } }
}
```

## Open WebUI — آزمایش نشده

نسخه‌ی 0.6.31 به بعد: Admin Settings ← External Tools ← افزودن، نوع «MCP (Streamable HTTP)»، احراز هویت Bearer ([مستندات](https://docs.openwebui.com/features/extensibility/mcp)). اگر Open WebUI داخل Docker است، نشانیِ پل از دید آن را (مثلاً `host.docker.internal:*`) به `ATB_EXTRA_HOSTS` بیفزایید و مطمئن شوید پل روی آن نشانی در دسترس است.

## Hermes Agent — آزمایش نشده

`~/.hermes/config.yaml` و سپس `/reload-mcp` ([مستندات](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference)):

```yaml
mcp_servers:
  terminal-bridge:
    url: http://127.0.0.1:8750/mcp
    headers:
      Authorization: "Bearer ${ATB_AGENT_TOKEN}"
```

## Ollama، vLLM و هر API سازگار با OpenAI

```bash
pip install httpx
export ATB_URL=http://127.0.0.1:8750 ATB_AGENT_TOKEN=TOKEN
export OPENAI_BASE_URL=http://127.0.0.1:11434/v1 OPENAI_MODEL=qwen2.5-coder
python examples/openai_agent.py "تست‌های پروژه‌ی /srv/app روی سرور demo را اجرا و خطاها را رفع کن"
```

مدل باید tool calling را پشتیبانی کند؛ کیفیت کار به مدل بستگی دارد. برای ساخت عامل خودتان: `GET /api/v1/openai/tools` و `POST /api/v1/openai/tool_calls`.

## خطاهای رایج

| نشانه | علت |
|---|---|
| 401 | توکن اشتباه، چرخش‌یافته یا توکن کاربر به‌جای توکن دستیار |
| 421 یا «Invalid Host header» روی `/mcp` | نام میزبان در `ATB_ALLOWED_HOSTS` / `ATB_EXTRA_HOSTS` نیست |
| 403 روی `/mcp` از کلاینت مرورگری | Origin را به `ATB_ALLOWED_ORIGINS` بیفزایید |
| فهرست سرورها خالی است | به دستیار روی هیچ سروری دسترسی داده نشده |
