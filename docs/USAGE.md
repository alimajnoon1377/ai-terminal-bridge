# Installation and usage

The dashboard UI is in Persian. Where this guide names a page or button, the on-screen Persian label follows in «quotes».

## Requirements

- **Bridge machine:** Docker with Compose; or Python 3.11+ and (to build the dashboard) Node.js 22.12+.
- **Target server:** Linux with SSH and SFTP, `bash`, `coreutils`, `util-linux` (for `setsid`), `procps`, `grep`, `find`, `tar`. Recommended: `bubblewrap` (sandbox), `tmux` (persistent terminal), `git`. Windows and macOS targets are untested, and the system tools either do not work on them or are not guaranteed to.

## Installing with Docker Compose

Linux:

```bash
cp .env.example .env
# set ATB_ADMIN_PASSWORD (at least 10 characters) in .env
docker compose up -d --build
```

Windows (PowerShell, with Docker Desktop):

```powershell
cd C:\path\to\ai-terminal-bridge
Copy-Item .env.example .env
notepad .env          # set ATB_ADMIN_PASSWORD
docker compose up -d --build
```

Then open <http://127.0.0.1:8750> and sign in as `admin`.

> Building with the official Docker Hub images (`node:22-slim`, `python:3.13-slim`) and running on Windows with Docker Desktop was done on one machine. Docker Desktop must be running before `docker compose up` (`docker desktop start`).

If your network cannot reach Docker Hub, set the base images from another registry you trust with `NODE_IMAGE` and `PYTHON_IMAGE` (and `CADDY_IMAGE` and `POSTGRES_IMAGE` for the profiles) in `.env`.

If you want to add the Docker host machine itself (Linux) as a server, its address is `host.docker.internal`, and SSH must be reachable on it.

### Remote access over HTTPS

Point your domain's DNS at the server, open ports 80 and 443, and set `ATB_DOMAIN=bridge.example.com` in `.env`:

```bash
docker compose --profile https up -d --build
```

Caddy obtains the certificate automatically. If you do not want it to be public, keep the default `127.0.0.1` and use an SSH tunnel or a VPN: `ssh -L 8750:127.0.0.1:8750 user@bridge-host`.

### PostgreSQL

In `.env` (special characters in the password must be encoded in the URL):

```ini
POSTGRES_PASSWORD=a-password
ATB_DATABASE_URL=postgresql+asyncpg://atb:a-password@postgres:5432/atb
```

```bash
docker compose --profile postgres up -d --build
```

The bridge may restart a few times until PostgreSQL is ready. Data is not migrated from SQLite automatically.

## Native installation (without Docker)

Linux:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install ./backend
(cd frontend && npm ci && npm run build)
export ATB_ADMIN_PASSWORD='at-least-ten-characters' ATB_STATIC_DIR="$PWD/frontend/dist"
atb serve
```

Windows PowerShell (**untested**):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install .\backend
cd frontend; npm ci; npm run build; cd ..
$env:ATB_ADMIN_PASSWORD = "at-least-ten-characters"
$env:ATB_STATIC_DIR = "$PWD\frontend\dist"
atb serve
```

Data is stored in `ATB_DATA_DIR` (default: `data` in the directory you run it from). On Windows the permissions of the `master.key` file are not restricted the way they are on Linux; restrict access to the folder yourself. All settings: [CONFIG.md](CONFIG.md).

## Preparing the target server

On the server (Ubuntu/Debian):

```bash
sudo apt install bubblewrap tmux git
sudo useradd --create-home --shell /bin/bash atb
sudo usermod -p '*' atb                      # no password login, without locking the account
sudo install -d -o atb -g atb /srv/projects  # projects live outside home
```

Check that the sandbox works:

```bash
sudo -u atb bwrap --unshare-pid --unshare-net --ro-bind / / --proc /proc true && echo OK
```

**Ubuntu 23.10 and later** may restrict unprivileged user namespaces with AppArmor ([official explanation](https://ubuntu.com/blog/ubuntu-23-10-restricted-unprivileged-user-namespaces)), which makes the command above fail. One reported fix for 24.04 ([source](https://github.com/microsoft/vscode/issues/316046); not tested here):

```bash
sudo apt install apparmor-profiles
sudo cp /usr/share/apparmor/extra-profiles/bwrap-userns-restrict /etc/apparmor.d/
sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
```

If the sandbox does not work, `developer` commands require human approval, and grants with `force_sandbox` do not run commands.

### Adding a server

1. Dashboard → **Servers** («سرورها») → **Add server** («افزودن سرور»): name, host, port, user `atb`, authentication **Generate new key** («ساخت کلید جدید»).
2. Add the public key that is shown to the server:

   ```bash
   sudo install -d -m 700 -o atb -g atb /home/atb/.ssh
   echo 'ssh-ed25519 AAAA...' | sudo tee -a /home/atb/.ssh/authorized_keys
   sudo chown atb:atb /home/atb/.ssh/authorized_keys && sudo chmod 600 /home/atb/.ssh/authorized_keys
   ```

3. Click **Fetch fingerprint** («دریافت اثر انگشت»), compare it with the output of `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` **on the server itself**, and confirm.
4. Test the connection («آزمایش اتصال»).

## Giving assistants access

Dashboard → **Assistants** («دستیارها و دسترسی») → create a token («ساخت توکن») → choose a server → **Grant access** («اعطای دسترسی»). Access is separate for every (assistant, server) pair, and the paths act as the "project" scope.

| Level | Behaviour |
|---|---|
| `read_only` | only reads files and system information |
| `developer` | writes in the allowed paths and runs code in the sandbox |
| `operator` | runs commands and manages the allowed services (no sandbox unless `force_sandbox`) |
| `approval_required` | every change waits for approval |
| `autonomous` | no approval within the granted scope; destructive operations only with `auto_approve_destructive` |

The exact decision table is in [TOOLS.md](TOOLS.md#decision-matrix). Connecting each client: [CLIENTS.md](CLIENTS.md).

### Practical notes

- **Installing dependencies in the sandbox:** the network is off by default; turn on `sandbox_network` for `pip`/`npm`/`git pull`/`git push`.
- **Tools installed in home** (such as nvm, pyenv, `~/.local`) are not visible in the sandbox (`HOME=/tmp/home` and a system `PATH`). Install the tool system-wide, or create the virtual environment inside the project folder.
- A **writable path** must not contain the SSH user's home, and must not be or contain a symlink.
- `git clone/push` over SSH has no access to the key inside the sandbox (`~/.ssh` is hidden); use HTTPS with a limited credential, or do it yourself.
- At levels without a sandbox, `git commit` needs a git identity configured on the server.
- The allowed-programs list also applies to `execute_command`, `run_tests` and git write operations; put the programs you need (`git`, `python3`, `make`, ...) in the list.
- Leave `max_memory_mb` at zero for Node projects: in testing, with a limit of 2 to 8 GB, `fetch` in Node 22 failed with a memory error.

## Daily use

- **Approvals:** the Approvals page («تأیید عملیات»); read the whole preview.
- **File changes and snapshots:** the Files page («فایل‌ها و ویرایشگر»), Changes («تغییرات»), to roll back.
- **Emergency stop** (administrator): the button at the top of the dashboard («توقف اضطراری»); assistants' commands are stopped and their calls rejected.
- **Users:** the Settings page («تنظیمات و امنیت»), or:

  ```bash
  docker compose exec bridge atb create-user NAME --admin
  ```

  (A forgotten administrator password is also solved by creating another administrator.)
- **Checking the audit log:** `docker compose exec bridge atb verify-audit`

## Backups and upgrades

Losing `master.key` (or `ATB_MASTER_KEY`) without a backup means losing the stored SSH keys. A consistent backup of the SQLite version:

```bash
docker compose stop bridge
docker compose cp bridge:/data ./atb-backup
docker compose start bridge
```

The backup folder contains the master key and the database; keep it safe, and preferably archive the key separately. To upgrade, replace the files and run `docker compose up -d --build`. This version has no automatic database migration.

## Troubleshooting

| Message | Solution |
|---|---|
| `HOST KEY MISMATCH` | the server's key has changed; find out why, then confirm the fingerprint again |
| `contains the SSH user's home directory` | grant a project folder instead of home |
| `is or contains a symbolic link` | use the real path in the grant |
| `bubblewrap does not work on the server` | see [Preparing the target server](#preparing-the-target-server) |
| `only these programs are allowed` | fix the allowed-programs list |
| `passphrase-protected keys are not supported` | create a separate key without a passphrase for the bridge |
| The dashboard does not open (native install) | `ATB_STATIC_DIR` must point to the built `frontend/dist` |
| `no users exist yet` | set `ATB_ADMIN_PASSWORD`, or run `atb create-user NAME --admin` |

Logs: `docker compose logs -f bridge`.
