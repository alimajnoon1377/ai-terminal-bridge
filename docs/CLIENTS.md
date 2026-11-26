# Connecting assistants

First, in the dashboard (Persian UI): **Assistants** («دستیارها و دسترسی») → create a token → **Grant access** («اعطای دسترسی») on a server. The token is shown only once. Create a separate token for every assistant.

The examples assume the address `http://127.0.0.1:8750`.

## Compatibility status

| Client | Method | Status |
|---|---|---|
| Claude Code (version 2.1.332) | HTTP and stdio | ✅ connection and handshake tested; no model was called |
| Gemini CLI (version 0.71.1) | HTTP and stdio | ✅ connection and handshake tested; no model was called |
| Codex CLI (version 0.172.0) | HTTP and stdio | ✅ connection and listing of 25 tools tested; no model was called |
| MCP Python SDK, REST, WebSocket, OpenAI format, CLI | — | ✅ tool calls in the automated tests (a scripted model, not a real one) |
| Claude Desktop (Windows) | local stdio | ✅ on one machine with the MSIX package, version 6.28899.6: several tools ran with a real model (listed in the Claude Desktop section) |
| Claude (web and mobile, custom connector) | public HTTPS | ⚠️ untested; see the limitations below |
| Cursor, VS Code, Open WebUI, Hermes Agent | HTTP | ⚠️ untested; based on the official documentation |
| Ollama and local models | `examples/openai_agent.py` | ⚠️ the script was tested with a fake model, not with a real Ollama |
| ChatGPT (connector) | — | ❌ not supported |

## Claude Code

```bash
claude mcp add --transport http terminal-bridge http://127.0.0.1:8750/mcp \
  --header "Authorization: Bearer TOKEN"
# or stdio:
claude mcp add terminal-bridge -e ATB_URL=http://127.0.0.1:8750 -e ATB_AGENT_TOKEN=TOKEN -- atb mcp-stdio
claude mcp list
```

## Gemini CLI

```bash
gemini mcp add --scope user --transport http --header "Authorization: Bearer TOKEN" terminal-bridge http://127.0.0.1:8750/mcp
# or stdio:
gemini mcp add --scope user -e ATB_URL=http://127.0.0.1:8750 -e ATB_AGENT_TOKEN=TOKEN terminal-bridge atb mcp-stdio
```

In testing, Gemini CLI showed the server as connected only after the current folder was marked as trusted.

## Codex CLI

```bash
export ATB_AGENT_TOKEN=TOKEN
codex mcp add terminal-bridge --url http://127.0.0.1:8750/mcp --bearer-token-env-var ATB_AGENT_TOKEN
# or stdio:
codex mcp add terminal-bridge --env ATB_URL=http://127.0.0.1:8750 --env ATB_AGENT_TOKEN=TOKEN -- atb mcp-stdio
```

## Claude Desktop (local, stdio)

Put the `mcpServers` block below in `claude_desktop_config.json`. Quit the application completely **before editing** (Quit from the tray or menu bar icon), then open it again. If the file already holds other settings, add only the `mcpServers` key and keep the rest. Make sure no stray spaces surround the token (it causes an `Illegal header value` error).

**The file location on Windows** is usually `%APPDATA%\Claude\`, but packaged (MSIX) installations may use another place. On the machine that was tested, the application read this file from `%LOCALAPPDATA%\Claude-Data\<package name>\` (observed on one machine and one version). To find the data folder while the application is running:

```powershell
Get-CimInstance Win32_Process -Filter "Name='Claude.exe'" |
  ForEach-Object { if ($_.CommandLine -match '--user-data-dir=("[^"]+"|[^"]+?(?="|\s+(--|/)|$))') { $Matches[1].Trim('"') } } |
  Sort-Object -Unique
```

Tested with the Docker method below: connecting from a chat linked to the same computer, and the tools `list_servers`, `connect_server`, `create_directory`, `write_file` and `execute_command` (in the sandbox) on a real Linux server. Approval and rollback have not been tested through this route.

**If the bridge runs with Docker Compose** (no Python needed on the host). Replace the Compose file path with your own. If the application cannot find `docker`, put its full path in `command` (usually `C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe`):

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

**If `atb` is installed on the same machine** (`pip install ./backend`; this method has not been tested with Claude Desktop):

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

If Claude Desktop cannot find the program, put the full path of `atb.exe` in `command`.

## Claude on web and mobile (custom connector): untested

- The server must be reachable from Anthropic's infrastructure over **public HTTPS** (not localhost or a VPN) ([support article](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)).
- Authentication with a static token uses "Request headers", but the developer documentation describes it as a beta feature for a limited set of organizations ([remote MCP](https://claude.com/docs/connectors/custom/remote-mcp), [authentication](https://claude.com/docs/connectors/building/authentication)). Without that option it cannot connect, because the bridge has no OAuth.
- Exposing a bridge that reaches a server terminal to the public is a serious risk; prefer Claude Code or Desktop.

## ChatGPT: not supported

According to the [OpenAI documentation](https://developers.openai.com/plugins/build/auth), ChatGPT requires either OAuth 2.1 or no authentication for an MCP server, and does not send a static token. The bridge has no OAuth (and does not run without authentication). Use Codex CLI, or connect OpenAI models through `examples/openai_agent.py`.

## Cursor: untested

`~/.cursor/mcp.json` or `.cursor/mcp.json` ([documentation](https://cursor.com/docs/mcp)):

```json
{ "mcpServers": { "terminal-bridge": {
  "url": "http://127.0.0.1:8750/mcp",
  "headers": { "Authorization": "Bearer ${env:ATB_AGENT_TOKEN}" } } } }
```

## VS Code: untested

`.vscode/mcp.json` ([documentation](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)):

```json
{
  "inputs": [ { "type": "promptString", "id": "atb-token", "description": "Terminal bridge token", "password": true } ],
  "servers": { "terminal-bridge": {
    "type": "http", "url": "http://127.0.0.1:8750/mcp",
    "headers": { "Authorization": "Bearer ${input:atb-token}" } } }
}
```

## Open WebUI: untested

Version 0.6.31 and later: Admin Settings → External Tools → add, type "MCP (Streamable HTTP)", Bearer authentication ([documentation](https://docs.openwebui.com/features/extensibility/mcp)). If Open WebUI runs inside Docker, add the bridge address as it sees it (for example `host.docker.internal:*`) to `ATB_EXTRA_HOSTS`, and make sure the bridge is reachable at that address.

## Hermes Agent: untested

`~/.hermes/config.yaml`, then `/reload-mcp` ([documentation](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference)):

```yaml
mcp_servers:
  terminal-bridge:
    url: http://127.0.0.1:8750/mcp
    headers:
      Authorization: "Bearer ${ATB_AGENT_TOKEN}"
```

## Ollama, vLLM and any OpenAI-compatible API

```bash
pip install httpx
export ATB_URL=http://127.0.0.1:8750 ATB_AGENT_TOKEN=TOKEN
export OPENAI_BASE_URL=http://127.0.0.1:11434/v1 OPENAI_MODEL=qwen2.5-coder
python examples/openai_agent.py "Run the tests of the /srv/app project on the demo server and fix the errors"
```

The model must support tool calling; the quality of the work depends on the model. To build your own agent, use `GET /api/v1/openai/tools` and `POST /api/v1/openai/tool_calls`.

## Common errors

| Symptom | Cause |
|---|---|
| 401 | wrong or rotated token, or a user token instead of an assistant token |
| 421 or "Invalid Host header" on `/mcp` | the host name is not in `ATB_ALLOWED_HOSTS` / `ATB_EXTRA_HOSTS` |
| 403 on `/mcp` from a browser client | add the Origin to `ATB_ALLOWED_ORIGINS` |
| The server list is empty | the assistant has not been granted access to any server |
