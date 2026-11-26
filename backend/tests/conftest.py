"""Integration fixtures: a real OpenSSH server and a real bridge listening on a local port.

The SSH server is started by the test run. It logs in as the current user, or, when the
tests run as root, as the user named in ATB_TEST_USER.
"""

import asyncio
import getpass
import os
import pwd
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path

import asyncssh
import httpx
import pytest
import uvicorn
from sqlalchemy import select

from atb.app import create_app
from atb.config import Settings
from atb.db import Base

SSHD = shutil.which("sshd") or "/usr/sbin/sshd"
ADMIN_PASSWORD = "correct-horse-battery"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class SSHServer:
    def __init__(self, root: Path, user: str):
        self.root, self.user, self.port = root, user, free_port()
        self.authorized = root / "authorized_keys"
        self.process: subprocess.Popen | None = None

    def start(self, host_key_name: str = "host_key") -> None:
        host_key = self.root / host_key_name
        if not host_key.exists():
            asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(host_key))
            host_key.chmod(0o600)
        self.authorized.touch(exist_ok=True)
        self.authorized.chmod(0o644)
        config = self.root / "sshd_config"
        config.write_text(
            f"ListenAddress 127.0.0.1\nPort {self.port}\nHostKey {host_key}\nPidFile {self.root}/sshd.pid\n"
            f"AuthorizedKeysFile {self.authorized}\nStrictModes no\nUsePAM no\nPasswordAuthentication no\n"
            "Subsystem sftp internal-sftp\nMaxStartups 100:30:200\nMaxSessions 100\n"
        )
        if os.geteuid() == 0:
            os.makedirs("/run/sshd", exist_ok=True)
        self.process = subprocess.Popen([SSHD, "-D", "-e", "-f", str(config)], stderr=(self.root / "sshd.log").open("w"))
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                if self.process.poll() is not None:
                    break
                import time

                time.sleep(0.05)
        raise RuntimeError(f"sshd did not start: {(self.root / 'sshd.log').read_text()}")

    def stop(self) -> None:
        if self.process:
            self.process.terminate()
            self.process.wait(10)
            self.process = None


@pytest.fixture(scope="session")
def shared_root():
    root = Path(tempfile.mkdtemp(prefix="atb-it-"))
    root.chmod(0o755)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def ssh_user() -> str:
    if not os.path.exists(SSHD):
        pytest.skip("OpenSSH server is not installed")
    user = os.environ.get("ATB_TEST_USER") or getpass.getuser()
    if user == "root":
        pytest.skip("set ATB_TEST_USER to an unprivileged account")
    return user


@pytest.fixture(scope="session")
def sshd(shared_root, ssh_user):
    root = shared_root / "sshd"
    root.mkdir()
    server = SSHServer(root, ssh_user)
    server.start()
    yield server
    server.stop()


class Bridge:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.url = f"http://127.0.0.1:{settings.port}"
        self.ws_url = f"ws://127.0.0.1:{settings.port}"

    async def start(self) -> None:
        self.app = create_app(self.settings)
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.settings.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(200):
            if self.server.started:
                return
            if self.task.done():
                self.task.result()
            await asyncio.sleep(0.05)
        raise RuntimeError("bridge did not start")

    async def stop(self) -> None:
        self.server.should_exit = True
        await asyncio.wait_for(self.task, 20)

    @property
    def services(self):
        return self.app.state.services


class Client:
    def __init__(self, bridge: Bridge, token: str):
        self.token = token
        self.http = httpx.AsyncClient(base_url=bridge.url, headers={"Authorization": f"Bearer {token}"}, timeout=120)

    async def tool(self, name: str, /, *, expect: int = 200, **arguments) -> dict:
        response = await self.http.post(f"/api/v1/tools/{name}", json=arguments)
        assert response.status_code == expect, f"{name}: {response.status_code} {response.text}"
        return response.json()

    async def request(self, method: str, path: str, *, expect: int = 200, **kwargs):
        response = await self.http.request(method, path, **kwargs)
        assert response.status_code == expect, f"{method} {path}: {response.status_code} {response.text}"
        return response.json() if response.content else None


@pytest.fixture(scope="session")
async def bridge(shared_root):
    settings = Settings(
        data_dir=shared_root / "data", port=free_port(), admin_password=ADMIN_PASSWORD,
        approval_wait_seconds=0.2, job_poll_seconds=0.15, _env_file=None,
    )
    instance = Bridge(settings)
    await instance.start()
    yield instance
    await instance.stop()


async def database_dump(bridge: Bridge) -> bytes:
    """Every stored row as bytes, whichever database engine is in use."""
    parts = []
    async with bridge.services.db.session() as session:
        for table in Base.metadata.sorted_tables:
            parts.extend(repr(tuple(row)) for row in (await session.execute(select(table))).all())
    return "\n".join(parts).encode()


async def login(bridge: Bridge, username: str, password: str) -> Client:
    async with httpx.AsyncClient(base_url=bridge.url) as http:
        response = await http.post("/api/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200, response.text
    return Client(bridge, response.json()["token"])


@pytest.fixture(scope="session")
async def admin(bridge):
    client = await login(bridge, "admin", ADMIN_PASSWORD)
    yield client
    await client.http.aclose()


@pytest.fixture(scope="session")
async def server(admin, sshd):
    """A registered, host-key-pinned server."""
    record = await admin.request(
        "POST", "/api/servers", expect=201,
        json={"name": "testbox", "host": "127.0.0.1", "port": sshd.port, "username": sshd.user},
    )
    with sshd.authorized.open("a") as handle:
        handle.write(record["public_key"] + "\n")
    probe = await admin.request("POST", f"/api/servers/{record['id']}/probe")
    return await admin.request("POST", f"/api/servers/{record['id']}/trust", json={"fingerprint": probe["fingerprint"]})


class Workspace:
    """A project directory owned by the SSH user, plus a file outside it."""

    def __init__(self, base: Path, user: str):
        self.base = base
        self.project = base / "project"
        self.outside = base / "outside"
        self.user = user

    def write(self, relative: str, text: str, root: Path | None = None) -> Path:
        path = (root or self.project) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.own()
        return path

    def own(self) -> None:
        if os.geteuid() == 0:
            entry = pwd.getpwnam(self.user)
            for current, dirs, files in os.walk(self.base):
                for name in [current, *(os.path.join(current, n) for n in dirs + files)]:
                    os.lchown(name, entry.pw_uid, entry.pw_gid)


@pytest.fixture
def workspace(shared_root, ssh_user) -> Workspace:
    base = shared_root / f"ws-{uuid.uuid4().hex[:8]}"
    ws = Workspace(base, ssh_user)
    ws.project.mkdir(parents=True)
    ws.outside.mkdir()
    base.chmod(0o755)
    ws.write("hello.txt", "hello world\nsecond line\n")
    ws.write("secret.txt", "outside secret\n", root=ws.outside)
    return ws


@pytest.fixture
async def make_agent(bridge, admin, server, workspace):
    """Create an assistant with a grant on the test server; returns an API client for it."""
    created = []

    async def factory(level: str = "operator", **overrides) -> Client:
        agent = await admin.request("POST", "/api/agents", expect=201, json={"name": f"a-{uuid.uuid4().hex[:6]}"})
        config = {"level": level, "paths": [{"path": str(workspace.project), "mode": "rw"}], **overrides}
        await admin.request("PUT", f"/api/agents/{agent['id']}/grants/{server['id']}", json=config)
        client = Client(bridge, agent["token"])
        client.id = agent["id"]
        created.append(client)
        return client

    yield factory
    for client in created:
        await admin.request("DELETE", f"/api/agents/{client.id}", expect=204)
        await client.http.aclose()


@pytest.fixture
def approve(admin):
    async def decide(approval_id: str, ok: bool = True, reason: str = ""):
        return await admin.request(
            "POST", f"/api/approvals/{approval_id}/decision", json={"approve": ok, "reason": reason}
        )

    return decide
