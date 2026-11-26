"""Command execution: sandbox selection, risk assessment, output retrieval, termination."""

import posixpath

from pydantic import Field

from ..db import new_id
from ..errors import Conflict, NotFound, PolicyDenied
from ..policy import (
    Kind,
    Level,
    check_command,
    check_path,
    decide,
    destructive_reasons,
    is_within,
)
from ..ssh.files import canonical, is_dir, sftp_session, stat_or_none
from ..ssh.jobs import Sandbox
from .base import Args, Context, Operation, ServerArgs, tool

_MASK_DIRS = (".ssh", ".atb", ".aws", ".gnupg")


async def sandbox_available(ctx: Context) -> bool:
    probe = "bwrap --unshare-pid --unshare-net --ro-bind / / --proc /proc true >/dev/null 2>&1"
    return (await ctx.run(["sh", "-c", probe])).exit_code == 0


async def build_sandbox(ctx: Context, *, read_only: bool = False, network: bool | None = None) -> Sandbox:
    """Translate the grant's path rules into mounts, refusing layouts that could not confine anything."""
    ctx.require_paths()
    async with sftp_session(await ctx.connection()) as sftp:
        home = str(await sftp.realpath("."))
        grant = await ctx.resolved_grant(sftp)
        rw, ro, masked = [], [], []
        for rule in grant.paths:
            attrs = await stat_or_none(sftp, rule.path)
            if attrs is None:
                continue
            writable = rule.mode == "rw" and not read_only
            if writable and is_within(home, rule.path):
                raise PolicyDenied(
                    f"the writable path {rule.path} contains the SSH user's home directory, so a sandboxed command "
                    "could alter login scripts and escape. Grant a project directory instead."
                )
            if rule.path in ("/", "/proc", "/dev", "/sys") or (writable and rule.path in ("/etc", "/usr", "/bin", "/lib")):
                raise PolicyDenied(f"{rule.path} cannot be mounted into a sandbox")
            if any(is_within(rule.path, posixpath.join(home, name)) for name in _MASK_DIRS):
                raise PolicyDenied(f"{rule.path} is inside a credential directory and cannot be mounted")
            (rw if writable else ro).append(rule.path)
            if is_dir(attrs):
                bases = [rule.path] + ([home] if is_within(home, rule.path) else [])
                for candidate in (posixpath.join(base, name) for base in bases for name in _MASK_DIRS):
                    if candidate not in masked and await sftp.isdir(candidate):
                        masked.append(candidate)
        if not rw and not ro:
            raise PolicyDenied("none of the granted paths exist on the server")
    return Sandbox(tuple(rw), tuple(ro), tuple(masked), grant.sandbox_network if network is None else network)


async def plan_command(
    ctx: Context,
    command: str,
    *,
    cwd: str | None,
    timeout: int | None,
    wait_seconds: float,
    kind: Kind = Kind.EXEC,
    destructive: str = "",
    summary: str = "",
) -> Operation:
    """Common path for every tool that runs arbitrary code on the server."""
    grant = ctx.grant
    if grant.level == Level.READ_ONLY:
        decide(grant, kind)  # raises
    effective = check_command(command, grant)
    wants_sandbox = grant.level == Level.DEVELOPER or grant.force_sandbox
    sandbox = None
    if wants_sandbox:
        ctx.require_paths()
        if await sandbox_available(ctx):
            sandbox = await build_sandbox(ctx)
        elif grant.force_sandbox:
            raise PolicyDenied("this grant requires a sandbox, but bubblewrap does not work on the server")
    async with sftp_session(await ctx.connection()) as sftp:
        home = str(await sftp.realpath("."))
        directory = await canonical(sftp, cwd or ctx.default_dir(home), ctx.default_dir(home))
        if grant.paths and grant.level != Level.OWNER:
            check_path(directory, await ctx.resolved_grant(sftp), write=False)
        if not await sftp.isdir(directory):
            raise NotFound(f"working directory {directory} does not exist")
    reasons = [destructive] if destructive else []
    if ctx.actor.is_agent:
        reasons += destructive_reasons(command)
    limit = min(timeout or grant.max_runtime_seconds, grant.max_runtime_seconds)
    where = "sandboxed" if sandbox else "NOT sandboxed (full access of the SSH user)"
    preview = f"$ {effective}\n\ndirectory: {directory}\ntimeout: {limit}s\nisolation: {where}"

    async def run() -> dict:
        jobs = ctx.services.jobs
        if len(await jobs.running(ctx.actor.id, ctx.server.id)) >= grant.max_concurrent_jobs:
            raise Conflict(f"{grant.max_concurrent_jobs} commands are already running; wait for one or terminate it")
        job_id = new_id()
        job = await jobs.start(
            ctx.target, actor_type=ctx.actor.type, actor_id=ctx.actor.id, job_id=job_id,
            command=effective, stored_command=ctx.services.box.encrypt(effective, f"job:{job_id}"),
            cwd=directory, timeout=limit,
            output_limit=grant.max_output_kb * 1024, memory_mb=grant.max_memory_mb, sandbox=sandbox,
        )
        ctx.services.events.publish(
            "job_started", ctx.server.owner_id, job_id=job.id, server_id=ctx.server.id,
            actor=ctx.actor.name, command=effective[:4000],
        )
        if ctx.actor.is_agent and await ctx.services.is_stopped(ctx.actor.id):
            await jobs.terminate(job)
            raise PolicyDenied("assistant activity was stopped while this command was starting")
        return await job_result(ctx, job.id, 0, wait_seconds)

    return Operation(
        run=run, kind=kind, summary=summary or f"run: {effective[:200]}", preview=preview,
        destructive=", ".join(dict.fromkeys(reasons)), sandboxed=sandbox is not None, state=f"{directory}|{effective}",
    )


async def job_result(ctx: Context, job_id: str, offset: int, wait_seconds: float) -> dict:
    jobs = ctx.services.jobs
    job = await jobs.wait(job_id, wait_seconds) if wait_seconds else await jobs.get(job_id)
    output, next_offset, more = await jobs.read_output(job, offset)
    result = {
        "command_id": job.id,
        "status": job.status,
        "exit_code": job.exit_code,
        "sandboxed": job.sandboxed,
        "output": output,
        "next_offset": next_offset,
        "more_output": more,
    }
    if job.status == "running":
        result["next_step"] = "Call get_command_output with this command_id and next_offset."
    if next_offset >= job.output_limit:
        result["output_limit_reached"] = True
    return result


class ExecuteArgs(ServerArgs):
    command: str = Field(min_length=1, max_length=32000, description="Bash command line")
    cwd: str | None = Field(None, description="Working directory (defaults to your first writable path)")
    timeout_seconds: int | None = Field(None, ge=1, le=86400)
    wait_seconds: float = Field(20, ge=0, le=50, description="Return early with status 'running' after this long")


@tool("execute_command", ExecuteArgs, "Run a shell command on a server. Long commands continue in the background.")
async def execute_command(ctx: Context, args: ExecuteArgs) -> Operation:
    return await plan_command(
        ctx, args.command, cwd=args.cwd, timeout=args.timeout_seconds, wait_seconds=args.wait_seconds
    )


class OutputArgs(Args):
    command_id: str
    offset: int = Field(0, ge=0, description="next_offset from the previous call")
    wait_seconds: float = Field(10, ge=0, le=50, description="Wait up to this long for the command to finish")


class TerminateArgs(Args):
    command_id: str


async def own_job(ctx: Context, command_id: str):
    """Load a job the caller may see, and bind the context to its server."""
    job = await ctx.services.jobs.get(command_id)
    if ctx.actor.is_agent and job.actor_id != ctx.actor.id:
        raise NotFound("no such command")
    await ctx.services.bind_server(ctx, job.server_id)
    if not ctx.actor.is_agent:
        # Act on the connection that started the job.
        ctx.target = (await ctx.services.resolve_target(job.server_id))[0]
    return job


@tool("get_command_output", OutputArgs, "Fetch status and new output of a command started earlier.", read_only=True)
async def get_command_output(ctx: Context, args: OutputArgs) -> Operation:
    job = await own_job(ctx, args.command_id)

    async def run() -> dict:
        return await job_result(ctx, job.id, args.offset, args.wait_seconds)

    return Operation(run=run, summary=f"output of {job.id}")


@tool("terminate_command", TerminateArgs, "Stop a running command that you started.")
async def terminate_command(ctx: Context, args: TerminateArgs) -> Operation:
    job = await own_job(ctx, args.command_id)

    async def run() -> dict:
        finished = await ctx.services.jobs.terminate(job)
        return {"command_id": finished.id, "status": finished.status}

    # Stopping your own command is always permitted, whatever the access level.
    return Operation(run=run, kind=Kind.READ, summary=f"terminate {job.id}")


