"""Remote jobs that outlive the SSH connection (and the bridge itself).

Each command runs in its own session on the target under `~/.atb/jobs/<id>/`:
`run.sh` starts it, `out` receives combined output, `rc` receives the exit status.
The bridge only reads those files, so it can reattach after a network drop or restart.
"""

import asyncio
import logging
import posixpath
import shlex
from dataclasses import dataclass

from sqlalchemy import select

from ..db import Database, Job, now
from ..errors import BridgeError, NotFound, RemoteError
from ..events import EventBus
from ..redact import redact
from .client import SSHPool, Target
from .files import sftp_session

log = logging.getLogger(__name__)

FINAL_STATES = ("succeeded", "failed", "timed_out", "terminated", "lost")
CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class Sandbox:
    rw_paths: tuple[str, ...]
    ro_paths: tuple[str, ...]
    masked_dirs: tuple[str, ...]
    network: bool


def bwrap_argv(sandbox: Sandbox, cwd: str) -> list[str]:
    """bubblewrap arguments: system directories read-only, only granted paths visible."""
    argv = ["bwrap", "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
    if not sandbox.network:
        argv.append("--unshare-net")
    for path in ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/opt"):
        argv += ["--ro-bind-try", path, path]
    for path in (
        "/etc/alternatives", "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/passwd",
        "/etc/group", "/etc/hosts", "/etc/resolv.conf", "/etc/nsswitch.conf", "/etc/ssl", "/etc/pki",
        "/etc/ca-certificates", "/etc/gitconfig", "/etc/localtime",
    ):
        argv += ["--ro-bind-try", path, path]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home"]
    binds = [(path, "--ro-bind") for path in sandbox.ro_paths] + [(path, "--bind") for path in sandbox.rw_paths]
    # Parents first, so a nested read-only rule overrides its writable parent and vice versa.
    for path, flag in sorted(binds, key=lambda item: len(item[0])):
        argv += [flag, path, path]
    for path in sandbox.masked_dirs:
        argv += ["--tmpfs", path]
    argv += [
        "--clearenv", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
        "--setenv", "HOME", "/tmp/home", "--setenv", "LANG", "C.UTF-8", "--chdir", cwd, "--",
    ]
    return argv


def build_run_script(
    command: str, cwd: str, timeout: int, output_limit: int, memory_mb: int, sandbox: Sandbox | None
) -> str:
    prelude = f"ulimit -v {memory_mb * 1024} || exit 96\n" if memory_mb else ""
    script = f"{prelude}cd -- {shlex.quote(cwd)} || exit 97\n{command}\n"
    inner = ["bash", "-c", script]
    if sandbox:
        inner = bwrap_argv(sandbox, cwd) + inner
    return (
        "#!/usr/bin/env bash\n"
        'cd "$(dirname "$0")" || exit 98\n'
        "echo $$ > pid\n"
        f"{{ timeout -k 5 {int(timeout)} {shlex.join(inner)} 2>&1 </dev/null; echo $? > rc.tmp; }} "
        f"| stdbuf -o0 head -c {int(output_limit)} > out\n"
        "mv rc.tmp rc\n"
    )


class JobManager:
    def __init__(self, database: Database, pool: SSHPool, events: EventBus, resolve_target, poll_seconds: float = 0.4):
        self._db = database
        self._pool = pool
        self._events = events
        self._resolve_target = resolve_target  # async (server_id) -> (Target, owner_id)
        self._poll = poll_seconds
        self._watchers: dict[str, asyncio.Task] = {}
        self._done: dict[str, asyncio.Event] = {}

    async def start(
        self,
        target: Target,
        *,
        actor_type: str,
        actor_id: str,
        job_id: str,
        command: str,
        stored_command: str,
        cwd: str,
        timeout: int,
        output_limit: int,
        memory_mb: int = 0,
        sandbox: Sandbox | None = None,
    ) -> Job:
        conn = await self._pool.get(target, actor_id)
        script = build_run_script(command, cwd, timeout, output_limit, memory_mb, sandbox)
        async with sftp_session(conn) as sftp:
            home = str(await sftp.realpath("."))
            base = posixpath.join(home, ".atb")
            remote_dir = posixpath.join(base, "jobs", job_id)
            await sftp.makedirs(remote_dir, exist_ok=True)
            await sftp.chmod(base, 0o700)
            async with sftp.open(posixpath.join(remote_dir, "run.sh"), "w") as handle:
                await handle.write(script)
        job = Job(
            id=job_id, actor_type=actor_type, actor_id=actor_id, server_id=target.server_id,
            command=stored_command, cwd=cwd, remote_dir=remote_dir, timeout=timeout,
            output_limit=output_limit, sandboxed=sandbox is not None,
        )
        async with self._db.session() as session:
            session.add(job)
            await session.commit()
        launcher = f"setsid nohup bash {shlex.quote(remote_dir + '/run.sh')} >/dev/null 2>&1 </dev/null &"
        result = await self._pool.run(target, actor_id, launcher, timeout=30)
        if result.exit_code != 0:
            await self._finish(job.id, "failed", None)
            raise RemoteError(f"could not start the command: {result.stderr.strip()}")
        self._watch(job.id)
        return job

    async def get(self, job_id: str) -> Job:
        async with self._db.session() as session:
            job = await session.get(Job, job_id)
        if job is None:
            raise NotFound("no such command")
        return job

    async def running(self, actor_id: str | None = None, server_id: str | None = None) -> list[Job]:
        query = select(Job).where(Job.status == "running")
        if actor_id:
            query = query.where(Job.actor_id == actor_id)
        if server_id:
            query = query.where(Job.server_id == server_id)
        async with self._db.session() as session:
            return list(await session.scalars(query))

    async def read_output(self, job: Job, offset: int, limit: int = CHUNK_BYTES) -> tuple[str, int, bool]:
        """Return (text, next_offset, more_available). Offsets count bytes of raw output."""
        target, _ = await self._resolve_target(job.server_id)
        conn = await self._pool.get(target, job.actor_id)
        path = posixpath.join(job.remote_dir, "out")
        async with sftp_session(conn) as sftp:
            if not await sftp.exists(path):
                return "", offset, False
            size = (await sftp.stat(path)).size or 0
            if size <= offset:
                return "", offset, False
            async with sftp.open(path, "rb") as handle:
                data = await handle.read(min(limit, size - offset), offset)
        more = offset + len(data) < size
        if job.status == "running" or more:
            # Cut at a line boundary so a secret or UTF-8 character is not split in two.
            newline = data.rfind(b"\n")
            if newline >= 0:
                data = data[: newline + 1]
                more = offset + len(data) < size
            elif len(data) < limit:
                return "", offset, False
        return data.decode("utf-8", errors="replace"), offset + len(data), more

    async def wait(self, job_id: str, seconds: float) -> Job:
        job = await self.get(job_id)
        if job.status in FINAL_STATES:
            return job
        self._watch(job_id)
        try:
            await asyncio.wait_for(self._done[job_id].wait(), seconds)
        except TimeoutError:
            pass
        return await self.get(job_id)

    async def terminate(self, job: Job) -> Job:
        if job.status in FINAL_STATES:
            return job
        target, _ = await self._resolve_target(job.server_id)
        pid_file = shlex.quote(job.remote_dir + "/pid")
        # Signal every process in the job's session, TERM first and KILL as a fallback.
        script = (
            f"for i in 1 2 3 4 5 6; do [ -s {pid_file} ] && break; sleep 0.5; done\n"
            f"sid=$(cat {pid_file}) || exit 3\n"
            # Zombies are already dead; they only wait to be reaped.
            'members() { ps -e -o pid= -o sid= -o stat= | awk -v s="$sid" \'$2 == s && $3 !~ /^Z/ { print $1 }\'; }\n'
            "p=$(members); [ -n \"$p\" ] && kill -TERM $p 2>/dev/null\n"
            "for i in 1 2 3 4 5 6; do [ -z \"$(members)\" ] && exit 0; sleep 0.5; done\n"
            'p=$(members); [ -n "$p" ] && kill -KILL $p 2>/dev/null; sleep 0.3; [ -z "$(members)" ]\n'
        )
        result = await self._pool.run(target, job.actor_id, ["bash", "-c", script], timeout=30)
        if result.exit_code != 0:
            raise RemoteError(f"could not terminate the command: {result.stderr.strip() or 'processes still running'}")
        await self._finish(job.id, "terminated", None)
        return await self.get(job.id)

    async def resume(self) -> None:
        """Reattach to jobs that were running when the bridge last stopped."""
        for job in await self.running():
            self._watch(job.id)

    async def shutdown(self) -> None:
        for task in self._watchers.values():
            task.cancel()
        await asyncio.gather(*self._watchers.values(), return_exceptions=True)
        self._watchers.clear()

    def _watch(self, job_id: str) -> None:
        task = self._watchers.get(job_id)
        if task is None or task.done():
            self._done.setdefault(job_id, asyncio.Event())
            self._watchers[job_id] = asyncio.create_task(self._watch_loop(job_id))

    async def _watch_loop(self, job_id: str) -> None:
        offset, backoff, missing_since = 0, self._poll, None
        # Check short commands quickly, then settle to the configured interval.
        interval = min(0.05, self._poll)
        while True:
            try:
                job = await self.get(job_id)
                if job.status in FINAL_STATES:
                    break
                target, owner_id = await self._resolve_target(job.server_id)
                state = await self._probe(job, target)
                secrets = (target.secret,) if target.auth_type == "password" else ()
                text, offset, _ = await self.read_output(job, offset)
                if text:
                    self._events.publish("job_output", owner_id, job_id=job.id, data=redact(text, secrets))
                if state == "gone":
                    # Allow a moment for the exit status to be renamed into place.
                    missing_since = missing_since or now()
                    if now() - missing_since > 3:
                        await self._finish(job.id, "lost", None)
                        break
                elif state != "running":
                    while True:  # drain remaining output
                        fresh = await self.get(job_id)
                        fresh.status = "succeeded"
                        text, offset, more = await self.read_output(fresh, offset)
                        if text:
                            self._events.publish("job_output", owner_id, job_id=job.id, data=redact(text, secrets))
                        if not more:
                            break
                    code = int(state)
                    status = "succeeded" if code == 0 else "timed_out" if code in (124, 137) and now() - job.created_at >= job.timeout - 1 else "failed"
                    await self._finish(job.id, status, code)
                    break
                else:
                    missing_since = None
                backoff, interval = interval, min(interval * 2, self._poll)
            except NotFound:
                break
            except (BridgeError, ValueError) as exc:
                log.warning("job %s: %s; retrying", job_id, exc)
                backoff = min(backoff * 2, 30)
            await asyncio.sleep(backoff)

    async def _probe(self, job: Job, target: Target) -> str:
        """Return the exit status as text, 'running', or 'gone'."""
        directory = shlex.quote(job.remote_dir)
        starting = "running" if now() - job.created_at < 10 else "gone"
        script = (
            f"cd {directory} 2>/dev/null || {{ echo gone; exit; }}\n"
            "if [ -f rc ]; then cat rc; "
            'elif [ -f pid ]; then kill -0 "$(cat pid)" 2>/dev/null && echo running || { [ -f rc ] && cat rc || echo gone; }; '
            f"else echo {starting}; fi\n"
        )
        result = await self._pool.run(target, job.actor_id, ["sh", "-c", script], timeout=30)
        return result.stdout.strip() or "gone"

    async def _finish(self, job_id: str, status: str, code: int | None) -> None:
        async with self._db.session() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in FINAL_STATES:
                return
            job.status, job.exit_code, job.finished_at = status, code, now()
            await session.commit()
            server_id = job.server_id
        event = self._done.setdefault(job_id, asyncio.Event())
        event.set()
        try:
            _, owner_id = await self._resolve_target(server_id)
        except BridgeError:
            return
        self._events.publish("job_finished", owner_id, job_id=job_id, status=status, exit_code=code)
