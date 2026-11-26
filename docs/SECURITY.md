# Security and threat model

**Summary:** the bridge blocks unwanted access, records everything and hands sensitive operations to a human for approval; but **the command filter is not a security boundary.** The real boundaries are:

1. the Linux user the bridge connects to the server as (whatever that user can do is the ceiling of the damage),
2. path checks in the file tools (over SFTP, after symlinks are resolved on the server itself),
3. the sandbox (bubblewrap) for commands,
4. human approval.

The software has not been independently security audited.

## Assets and trust boundaries

| Asset | Location | Protection |
|---|---|---|
| SSH private keys and passwords | bridge database | AES-256-GCM with per-record associated data; never returned by the API |
| Master key | `master.key` (mode 0600) or `ATB_MASTER_KEY` | outside the database; verified at start-up |
| User passwords | database | argon2 |
| Session and assistant tokens | database | SHA-256 hash only (tokens are 256 random bits) |
| Command text, approval previews, previous file versions | database | encrypted |
| Activity log (audit) | database | hash chain; text is masked before being recorded, and file bodies are recorded only as hashes |

Untrusted actors: **the AI model itself** and any text it reads (code, logs, command output), anyone who obtains an assistant token, and network clients. Trusted: the bridge administrator, the machine the bridge runs on, and installed plugins.

Whoever takes over the bridge machine (the database plus the master key) has access to every server. Protect that machine like a bastion host.

## Threats and responses

| Threat | Control | What remains |
|---|---|---|
| Prompt injection from server content | the access policy is enforced by the bridge, not in the prompt; nothing the model says gets past the policy; the model is told to treat content as data | **Cannot be solved.** A deceived model can do anything its grant allows, including sending data out if the network is open |
| Command injection in tools | inputs are schema validated and quoted with `shlex`; service/container names, git refs and URLs are restricted (`ext::` is rejected) | `execute_command` is a shell by nature |
| Leaving the allowed path | resolved on the server, then matched against the rules; an allowed root must not be a symlink | file tools only; a shell without the sandbox is not confined to paths |
| Escape through auto-run files | assistants may not write `.bashrc`, `.profile`, user systemd units and the like; the sandbox rejects writable paths that contain home | project code, Makefiles or settings that later run outside the sandbox (CI, a service, you) |
| Destructive commands | a few patterns are always denied (`rm /`, `mkfs`, writing to a disk, fork bomb); "destructive" patterns require approval | **Can be bypassed** with encoding, a script or another language |
| Deceptive approvals | exact preview (command or diff) without masking; invisible and bidirectional characters are made visible in the dashboard; an approval is single use and tied to the arguments and file state | a human may approve without reading; a command such as `./deploy.sh` does not show the script's content |
| Secret leakage | known patterns are masked in output to assistants; sensitive paths are denied | **Can be bypassed** (for example with `base64`); the process list and `docker inspect` may contain secrets |
| Stolen assistant token | limited to the same grants; rotation and immediate suspension; rate limit | usable until revoked |
| MITM on SSH | the host key is pinned after human confirmation; a changed key is rejected | no protection if you confirm the fingerprint without comparing it |
| DNS rebinding and browsers | Host/Origin checks for `/mcp`, strict CSP, token in a header (not a cookie) | `/api/docs` loads files from a CDN and has no CSP |
| Password guessing | temporary lockout after 8 failures in 5 minutes per IP and user name pair | distributed attacks; there is no two-factor sign-in |
| Resource consumption | execution timeout, output cap, concurrent command cap, rate limit, request size cap | server CPU and disk are not limited; use cgroups or operating system quotas |

## Specific limitations (read these)

- **`operator`, `approval_required` and `autonomous` run commands without the sandbox** unless `force_sandbox` is on. In that case path rules restrict only the file tools and the command's starting directory.
- **Race condition (TOCTOU):** a `developer` assistant with a background command running can swap symlinks at the same time and get around the file tools' path check.
- Inside the sandbox, only top-level sensitive folders (at the root of the allowed paths and of home) are hidden; a sensitive file nested inside a project is visible.
- The sandbox network is off by default. `sandbox_network` opens the server's **entire** network (including its local services); there is no destination filter.
- Docker access is effectively equivalent to root. `use_sudo_for_services` needs a passwordless sudo rule; limit that rule to the `systemctl` commands you need.
- A process that detaches itself with `setsid` may survive termination (and the emergency stop). In the sandbox, a separate PID namespace and `--die-with-parent` limit this.
- Audit: deletion of the latest records can be detected only if you keep the last hash (the `head` field of `atb verify-audit`) outside the system. A human user's read-only browsing and the keys typed in the terminal are not recorded (opening and closing the terminal is).
- The emergency stop stops **assistants** only, not human users.
- The request size limit in the bridge itself relies on `Content-Length`; the Caddy configuration enforces it separately.
- OAuth, two-factor sign-in and passphrase-protected SSH keys are not supported.
- Plugins run inside the bridge process with full trust.
- `max_memory_mb` is enforced with `ulimit -v` (virtual memory), not real memory; some programs, such as Node, fail below a few gigabytes.

## Recommended configuration

1. On every server, create a separate user without sudo for the bridge ([guide](USAGE.md#preparing-the-target-server)).
2. Start with `developer` (sandbox) or `approval_required`; grant `autonomous` only on a disposable machine.
3. Keep paths as narrow as possible.
4. Keep the bridge on `127.0.0.1` or a VPN; if you expose it publicly, always use HTTPS.
5. Back up `master.key` separately from the database.
6. Read the whole preview before approving.

## Reporting a vulnerability

See the [security policy](../.github/SECURITY.md). Please do not publish details in a public issue.
