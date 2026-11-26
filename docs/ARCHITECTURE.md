# Architecture

## Overview

```
Claude Code / Gemini CLI / Codex / MCP clients ──┐  MCP (HTTP or stdio)
OpenAI-compatible models (local or cloud) ───────┤  REST: /api/v1/openai/*
Scripts and CLI ─────────────────────────────────┤  REST / WebSocket
Web dashboard (human) ───────────────────────────┘  REST / WebSocket
                         │
                 ┌───────▼──────────────────────────────────┐
                 │ Bridge (FastAPI, one async process)      │
                 │  Authentication ► Gateway ► Policy       │
                 │                     │        └► Approval │
                 │                     ▼                    │
                 │   Tools ► SSH pool / SFTP / jobs         │
                 │   Audit · encryption · secret masking    │
                 │   SQLite or PostgreSQL                   │
                 └───────┬──────────────────────────────────┘
                         │ SSH (pinned host key)
                 Linux servers  (bubblewrap, tmux, ~/.atb)
```

**No agent is installed** on the target server; only SSH/SFTP and ordinary Linux programs.

## A single policy enforcement point

Every interface ends up in `Gateway.call(actor, tool, arguments)`:

1. Emergency stop, assistant suspension and rate limit (for assistants)
2. Input validation with Pydantic (unknown fields are rejected)
3. Finding the server and that assistant's grant on it
4. The tool function only builds an `Operation` (kind, preview, state) and **changes nothing yet**
5. `decide` returns one of three results: allow, require approval, deny
6. Execution, secret masking in the output, audit record, live event

An approval is bound to a fingerprint of the assistant, server, tool, arguments, file state and grant. It is used once, and everything is checked again before execution.

## Components

| Path | Responsibility |
|---|---|
| `backend/atb/gateway.py` | policy enforcement point and approvals |
| `backend/atb/policy.py` | levels, path and command rules (pure functions) |
| `backend/atb/tools/` | tools; plugins come from the `atb.tools` entry point group |
| `backend/atb/ssh/` | connections, SFTP, persistent commands |
| `backend/atb/api/` | management REST, tool REST, WebSocket |
| `backend/atb/mcp_server.py` | MCP over HTTP and the stdio proxy |
| `backend/atb/{audit,crypto,redact,db,events}.py` | infrastructure |
| `frontend/` | React + TypeScript dashboard (xterm.js, Monaco), right-to-left, light/dark themes, no external requests |

## Persistent commands and reconnection

Each command is written as a script in `~/.atb/jobs/<id>/`, run with `setsid nohup`, and writes its output and exit code to the same folder. As a result:

- Losing SSH, losing the assistant or **restarting the bridge** does not stop the command; the bridge follows it again after it comes back up.
- Output is read by offset.
- The time limit is enforced with `timeout` and the output cap with `head -c`, on the server itself.

Job folders and snapshots in `~/.atb` are not cleaned up automatically.

The interactive terminal is for humans only; if `tmux` is on the server, the session survives a disconnect. Assistants have no interactive PTY (programs that need interactive input do not work; the standard input of commands is `/dev/null`).

## Technical decisions

| Choice | Reason | Cost |
|---|---|---|
| Python + FastAPI + AsyncSSH | suggested by the original specification; async SSH/SFTP and one lightweight process | a single bridge instance; no horizontal scaling |
| SQLite by default, PostgreSQL optional | simple installation on a small VPS | no automatic schema migration |
| Stateless MCP with JSON responses | proxy compatibility and simplicity | no server-initiated MCP notifications |
| In-memory state (rate limit, sign-in lockout, events) | simplicity | reset on restart; one worker only |
| bubblewrap instead of a container per command | lightweight, no root or daemon | needs user namespaces enabled |
| Persistent commands using files on the target | no agent to install | status is obtained by polling |

## Extending with plugins

A complete example is in [examples/plugin](../examples/plugin/atb_plugin_example/__init__.py):

```bash
pip install ./examples/plugin    # in the bridge's environment, then restart
```

Two rules: check every path with `ctx.resolve`, and cause no side effects before `Operation.run`. A plugin runs with full trust.
