# AI Terminal Bridge — backend

Python package `ai-terminal-bridge`: REST, WebSocket and MCP interfaces that let AI assistants work on SSH servers under a policy a human controls. Command: `atb`.

Installation, security notes and API reference are in the repository root (`README.md` and `docs/`).

```bash
pip install -e ".[dev]"
ATB_TEST_USER=someuser python -m pytest   # needs Linux with sshd; see docs/SCENARIOS.md
ruff check atb tests scripts
```
