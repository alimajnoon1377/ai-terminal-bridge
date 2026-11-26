# AI Terminal Bridge

A self-hosted, open-source bridge that connects AI assistants to the terminal and files of Linux servers (over SSH) through **MCP, REST, WebSocket, OpenAI-compatible tool calling and a CLI**, with permission levels, human approval, an activity log and a web dashboard. The dashboard UI is currently in **Persian**.

Status: version `0.1.0`. The core is covered by automated tests over real SSH, but it has not been used in production and has **not been independently security audited**. Read [what has and has not been tested](#testing-status) and the [security limitations](docs/SECURITY.md) before using it on a server you care about.

## Quick start

```bash
cp .env.example .env          # Windows: Copy-Item .env.example .env
# set ATB_ADMIN_PASSWORD in .env (at least 10 characters)
docker compose up -d --build
```

> It has been built and run on Windows with Docker Desktop and the official Docker Hub images (one machine).

1. Open <http://127.0.0.1:8750> and sign in as `admin`.
2. **Servers** («سرورها»): add a server, put the public key on it and confirm the fingerprint ([preparing the server](docs/USAGE.md#preparing-the-target-server)).
3. **Assistants** («دستیارها و دسترسی»): create a token and grant access.
4. Connect an assistant, for example Claude Code:

```bash
claude mcp add --transport http terminal-bridge http://127.0.0.1:8750/mcp --header "Authorization: Bearer TOKEN"
```

Then ask the assistant: "Use the terminal-bridge tools to list the servers and run the project's tests."

## Documentation

| Document | Topic |
|---|---|
| [Installation and usage](docs/USAGE.md) | Docker, native install, target server, backups, troubleshooting |
| [Connecting assistants](docs/CLIENTS.md) | Claude, Gemini, Codex, Cursor, VS Code, Open WebUI, Ollama and more |
| [Security](docs/SECURITY.md) | Threat model and limitations |
| [Architecture](docs/ARCHITECTURE.md) | Components and technical decisions |
| [Scenarios](docs/SCENARIOS.md) | Practical examples and their tests |
| [Tools](docs/TOOLS.md) · [API](docs/API.md) · [Configuration](docs/CONFIG.md) | Reference (generated from the code) |

## Feature status

✅ built and tested · 🟡 partial or untested (explained in the same row) · ❌ not built

### Capabilities

| Feature | Status |
|---|---|
| SSH connection, key or password, host key pinning | ✅ keys in the automated tests; passwords in a separate test against a real sshd |
| Linux targets | ✅ |
| Windows / macOS targets | 🟡 untested; the commands and system tools are written for Linux |
| Command execution, incremental output, termination, long-running commands | ✅ |
| Read, create, edit, delete, upload and download files | ✅ (up to 5 MB) |
| Move/rename files | 🟡 no dedicated tool; use `execute_command` (`mv`) |
| Project management | 🟡 through allowed paths, files and git; there is no separate "project" entity |
| Installing dependencies | 🟡 by running commands; sandbox network access was tested, installing a real package from the internet was not |
| Running scripts and tests | ✅ with `make`/`unittest`; 🟡 the `pytest`, `npm`, `go` and `cargo` paths of `run_tests` are not individually tested |
| Git | ✅ local (init, status, add, commit, log); 🟡 clone/pull/push with a remote repository are untested |
| Docker and Compose | ✅ with a real Docker and a local image |
| Processes, resources, file and Docker logs | ✅ |
| systemd services and journal logs | 🟡 implemented, **untested** (the development environment had no systemd) |
| Interactive sessions | ✅ for humans (web terminal; persistent with tmux); ❌ for assistants |
| Several servers at once, resuming after a disconnect | ✅ |
| Snapshots and rollback | ✅ (snapshots up to 1 GB) |

### Interfaces and dashboard

| Feature | Status |
|---|---|
| MCP (HTTP and stdio), REST, WebSocket, CLI, OpenAI format | ✅ |
| Claude Code, Gemini CLI, Codex CLI | 🟡 real connections tested; no model performed any work |
| Claude Desktop (Windows) | ✅ several tools with a real model on a real server ([details](docs/CLIENTS.md)); approval and rollback untested through this route |
| Cursor, VS Code, Open WebUI, Hermes, Ollama | 🟡 guide provided, untested |
| ChatGPT | ❌ requires OAuth |
| Servers, assistants, grants, tokens, approvals | ✅ |
| Live terminal, file manager, code editor, command history, live activity | ✅ |
| CPU, RAM, disk and network monitoring | ✅ |
| Light and dark themes, responsive layout | ✅ |
| English dashboard UI | ❌ Persian only |
| Reviewing file changes before they are applied | 🟡 for operations that need approval and for your own edits; at levels without approval the change applies immediately (it can be rolled back) |
| List of SSH connections and sessions | 🟡 no dedicated page (commands and connection test are available) |
| Security settings | 🟡 password, users, emergency stop; rate limit and `deny_globs` only through the API |

### Access and security

| Feature | Status |
|---|---|
| Five permission levels per assistant and server | ✅ |
| Separate access per project | 🟡 through path rules (rw/ro); for a different level on the same server, create a second assistant |
| Separate access per user and RBAC | 🟡 two roles, `admin` and `user`; each user sees only their own resources; no custom roles |
| Encrypted secrets, SSH keys, password and token hashing | ✅ |
| Encrypted transport | ✅ SSH; HTTPS with Caddy (tested locally with the Ubuntu package, not on a public domain) |
| Secure sign-in | ✅ password and temporary lockout; ❌ two-factor sign-in and OAuth |
| Sandbox | ✅ bubblewrap for `developer` and `force_sandbox`; ❌ isolated execution with Docker |
| Command and path restrictions, command injection | ✅ (the command filter is not a security boundary) |
| Prompt injection | 🟡 impact is only reduced through permissions and approval; there is no complete solution |
| Secret masking | 🟡 pattern based; can be bypassed |
| Resource and time limits | 🟡 time, output, concurrency, rate; memory optional; ❌ CPU and disk |
| Audit log, approval, immediate stop, rollback | ✅ |
| No root required | ✅ (nothing stops you from adding the root user yourself; `connect_server` reports it) |

### Technical

Modular and async ✅ · self-hosted with no paid service ✅ · multiple users and assistants ✅ · plugins ✅ · API documentation ✅ · Docker Compose ✅ · small VPS 🟡 (resource use was measured, not run on a real VPS)

## Testing status

Development environment: Linux (Ubuntu), Python 3.13, Node 22, real OpenSSH and bubblewrap.

- **Automated tests:** 140 tests on SQLite and also on PostgreSQL 16 (including 7 Docker tests); `ruff` reports no errors.
- **Dashboard:** one complete scenario in a real browser (Chrome), from sign-in to approval, file editing, terminal, emergency stop and the mobile view.
- **Docker Compose:** in the development environment (with substitute base images): build, run, health, restart while a command is running, non-root user and read-only filesystem. On Windows with Docker Desktop and the official images: build, run, health, sign-in and adding a real Linux server.
- **Real model:** Claude, through Claude Desktop on Windows, listed servers, connected, created a folder, wrote a file and ran it in the sandbox.
- **Clients:** Claude Code 2.1.332, Gemini CLI 0.71.1, Codex CLI 0.172.0 (connection only, no model).
- **Untested:** approval and rollback with a real model, the other clients in the table, systemd, a public domain, the `https` and `postgres` Compose profiles, native installation on Windows (without Docker) and macOS.
- **GitHub CI:** only `ruff`, the unit and documentation tests, and the dashboard build; run the SSH tests on your own Linux machine.

These tests show that the controls work in the scenarios tested; they do not replace an independent penetration test.

## Performance

Measured on 2 cores and 7 GB of memory, with the bridge, sshd and the client on **one machine**, 100 requests per row. Latency is higher over a real network.

Bridge memory: about 117 MB idle, peaking at about 180 MB.

| Concurrency | `read_file` median/p95 (ms), req/s | `list_directory` | `execute_command` |
|---|---|---|---|
| 1 | 31/45, 31 | 25/34, 38 | 129/150, 8.1 |
| 4 | 96/122, 41 | 81/116, 48 | 223/304, 17 |
| 16 | 338/467, 47 | 312/403, 50 | 760/1125, 19 |

To reproduce: `python backend/scripts/benchmark.py --help`

## Layout

```
backend/    the bridge (Python/FastAPI), tests, scripts
frontend/   the dashboard (React/TypeScript)
docs/       documentation
examples/   an OpenAI-compatible agent and a sample plugin
deploy/     Caddy configuration
```

## Development

```bash
pip install -e "./backend[dev]"
cd frontend && npm ci && npm run dev      # dashboard, proxied to the bridge on 8750
python backend/scripts/gen_docs.py        # after changing tools, the API or settings
```

## License

[MIT](LICENSE). Licenses of the libraries and of the Vazirmatn font (OFL) bundled with the dashboard are listed in [third-party-licenses.txt](frontend/public/third-party-licenses.txt).
