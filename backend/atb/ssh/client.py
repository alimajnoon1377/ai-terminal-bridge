"""SSH connection pool with pinned host keys and automatic reconnection."""

import asyncio
import shlex
from dataclasses import dataclass

import asyncssh

from ..errors import RemoteError


@dataclass(frozen=True)
class Target:
    server_id: str
    host: str
    port: int
    username: str
    auth_type: str  # "key" or "password"
    secret: str
    host_key: str  # OpenSSH public key line pinned by the owner


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


async def fetch_host_key(host: str, port: int, timeout: float = 15.0) -> dict:
    """Read the server's host key without authenticating, so a human can verify it."""
    try:
        key = await asyncio.wait_for(asyncssh.get_server_host_key(host, port, config=None), timeout)
    except (OSError, asyncssh.Error, TimeoutError) as exc:
        raise RemoteError(f"cannot reach {host}:{port}: {exc or type(exc).__name__}") from exc
    if key is None:
        raise RemoteError(f"{host}:{port} did not present a host key")
    return {
        "host_key": key.export_public_key("openssh").decode().strip(),
        "fingerprint": key.get_fingerprint("sha256"),
        "algorithm": key.get_algorithm(),
    }


def fingerprint_of(host_key: str) -> str:
    return asyncssh.import_public_key(host_key).get_fingerprint("sha256") if host_key else ""


def generate_keypair(comment: str) -> tuple[str, str]:
    key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
    return key.export_private_key("openssh").decode(), key.export_public_key("openssh").decode().strip()


def public_key_from_private(private_key: str) -> str:
    return asyncssh.import_private_key(private_key).export_public_key("openssh").decode().strip()


class SSHPool:
    """One connection per (server, actor), so one actor's disconnect never affects another."""

    def __init__(self, connect_timeout: float = 15.0, attempts: int = 3):
        self._connections: dict[tuple[str, str], asyncssh.SSHClientConnection] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._timeout = connect_timeout
        self._attempts = attempts

    async def get(self, target: Target, actor_id: str) -> asyncssh.SSHClientConnection:
        key = (target.server_id, actor_id)
        async with self._locks.setdefault(key, asyncio.Lock()):
            conn = self._connections.get(key)
            if conn is not None and not conn.is_closed():
                return conn
            conn = await self._connect(target)
            self._connections[key] = conn
            return conn

    async def _connect(self, target: Target) -> asyncssh.SSHClientConnection:
        if not target.host_key:
            raise RemoteError("the server's host key has not been verified yet")
        pinned = asyncssh.import_public_key(target.host_key)
        options: dict = {
            "host": target.host,
            "port": target.port,
            "username": target.username,
            # (trusted host keys, trusted CA keys, revoked keys)
            "known_hosts": ([pinned], [], []),
            "agent_path": None,
            "config": None,
            "keepalive_interval": 30,
            "keepalive_count_max": 3,
            "connect_timeout": self._timeout,
            "login_timeout": self._timeout,
        }
        if target.auth_type == "key":
            options["client_keys"] = [asyncssh.import_private_key(target.secret)]
        else:
            options["client_keys"] = None
            options["password"] = target.secret
        delay = 0.5
        for attempt in range(1, self._attempts + 1):
            try:
                return await asyncssh.connect(**options)
            except asyncssh.HostKeyNotVerifiable as exc:
                raise RemoteError(
                    "HOST KEY MISMATCH: the server presented a different key than the pinned one. "
                    "Connection refused. Verify the server before trusting the new key."
                ) from exc
            except asyncssh.PermissionDenied as exc:
                raise RemoteError(f"SSH authentication failed for user {target.username}") from exc
            except (OSError, asyncssh.Error, TimeoutError) as exc:
                if attempt == self._attempts:
                    raise RemoteError(f"SSH connection failed: {exc or type(exc).__name__}") from exc
                await asyncio.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    async def run(
        self, target: Target, actor_id: str, argv: list[str] | str, *, input: str | None = None, timeout: float = 60
    ) -> RunResult:
        """Run a short bridge-controlled command. Arguments given as a list are shell-quoted."""
        command = argv if isinstance(argv, str) else shlex.join(argv)
        conn = await self.get(target, actor_id)
        try:
            done = await conn.run(command, input=input, timeout=timeout, errors="replace")
        except asyncssh.TimeoutError as exc:
            raise RemoteError(f"remote command timed out after {timeout:.0f}s") from exc
        except (OSError, asyncssh.Error) as exc:
            await self.close(target.server_id, actor_id)
            raise RemoteError(f"SSH error: {exc}") from exc
        code = done.exit_status if done.exit_status is not None else -1
        return RunResult(code, str(done.stdout or ""), str(done.stderr or ""))

    def is_connected(self, server_id: str, actor_id: str) -> bool:
        conn = self._connections.get((server_id, actor_id))
        return conn is not None and not conn.is_closed()

    async def close(self, server_id: str, actor_id: str | None = None) -> int:
        keys = [k for k in self._connections if k[0] == server_id and actor_id in (None, k[1])]
        for key in keys:
            conn = self._connections.pop(key)
            conn.close()
            await conn.wait_closed()
        return len(keys)

    async def close_all(self) -> None:
        for server_id in {key[0] for key in self._connections}:
            await self.close(server_id)
