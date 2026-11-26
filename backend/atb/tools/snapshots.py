"""Directory snapshots and rollback of changes made through the bridge."""

import base64
import posixpath
import shlex

from pydantic import Field, model_validator

from ..db import Change, Snapshot, new_id
from ..errors import Conflict, NotFound, PolicyDenied, RemoteError, ValidationFailed
from ..policy import Kind, is_within
from ..ssh.files import read_bytes, sftp_session, write_atomic
from .base import Context, Operation, ServerArgs, tool
from .files import make_diff, sha

MAX_SNAPSHOT_KB = 1024 * 1024


class SnapshotArgs(ServerArgs):
    path: str = Field(description="Directory to archive")
    label: str = Field("", max_length=100)


@tool("create_snapshot", SnapshotArgs, "Archive a directory on the server so it can be restored later (up to 1 GiB).")
async def create_snapshot(ctx: Context, args: SnapshotArgs) -> Operation:
    path = await ctx.resolve(args.path, write=True)
    if path == "/":
        raise ValidationFailed("the file system root cannot be snapshotted")

    async def run() -> dict:
        home = await ctx.home()
        if path == home or home.startswith(path + "/"):
            raise ValidationFailed("snapshot a project directory, not one containing the home directory")
        size = await ctx.run(["du", "-sk", "--", path], timeout=120)
        if size.exit_code != 0:
            raise RemoteError(size.stderr.strip()[:500])
        if int(size.stdout.split()[0]) > MAX_SNAPSHOT_KB:
            raise ValidationFailed("the directory is larger than 1 GiB")
        snapshot_id = new_id()
        store = posixpath.join(home, ".atb", "snapshots")
        archive = posixpath.join(store, f"{snapshot_id}.tar.gz")
        script = (
            f"umask 077 && mkdir -p {shlex.quote(store)} && "
            f"tar -C {shlex.quote(posixpath.dirname(path))} -czf {shlex.quote(archive)} "
            f"-- {shlex.quote(posixpath.basename(path))}"
        )
        result = await ctx.run(["sh", "-c", script], timeout=175)
        if result.exit_code != 0:
            await ctx.run(["rm", "-f", "--", archive])
            raise RemoteError(f"snapshot failed: {result.stderr.strip()[:500]}")
        async with ctx.services.db.session() as session:
            session.add(Snapshot(id=snapshot_id, actor_id=ctx.actor.id, server_id=ctx.server.id, path=path,
                                 archive=archive, label=args.label))
            await session.commit()
        return {"snapshot_id": snapshot_id, "path": path, "label": args.label}

    return Operation(run=run, kind=Kind.WRITE, summary=f"snapshot {path}", state=path)


class RollbackArgs(ServerArgs):
    change_id: str | None = None
    snapshot_id: str | None = None

    @model_validator(mode="after")
    def one_of(self):
        if bool(self.change_id) == bool(self.snapshot_id):
            raise ValueError("provide exactly one of change_id or snapshot_id")
        return self


async def load_owned(ctx: Context, model, record_id: str):
    async with ctx.services.db.session() as session:
        record = await session.get(model, record_id)
    if record is None or record.server_id != ctx.server.id or (ctx.actor.is_agent and record.actor_id != ctx.actor.id):
        raise NotFound(f"no such {model.__name__.lower()}")
    return record


@tool("rollback_changes", RollbackArgs, "Undo a file change (change_id) or restore a directory snapshot (snapshot_id).")
async def rollback_changes(ctx: Context, args: RollbackArgs) -> Operation:
    if args.change_id:
        return await rollback_change(ctx, await load_owned(ctx, Change, args.change_id))
    return await restore_snapshot(ctx, await load_owned(ctx, Snapshot, args.snapshot_id))


async def rollback_change(ctx: Context, change: Change) -> Operation:
    if change.rolled_back:
        raise Conflict("this change was already rolled back")
    path = await ctx.resolve(change.path, write=True)
    if path != change.path:
        raise Conflict("the path now resolves elsewhere; rollback refused")
    before = base64.b64decode(ctx.services.box.decrypt(change.before_enc, f"change:{change.id}")) if change.existed else None
    async with sftp_session(await ctx.connection()) as sftp:
        current = await read_bytes(sftp, path) if await sftp.exists(path) else None
    if sha(current) != change.after_sha:
        raise Conflict("the file has been modified since that change; rollback would lose newer work")

    async def run() -> dict:
        async with sftp_session(await ctx.connection()) as sftp:
            latest = await read_bytes(sftp, path) if await sftp.exists(path) else None
            if sha(latest) != change.after_sha:
                raise Conflict("the file changed while the request was being processed")
            if before is None:
                await sftp.remove(path)
            else:
                await write_atomic(sftp, path, before, change.mode)
        async with ctx.services.db.session() as session:
            row = await session.get(Change, change.id)
            row.rolled_back = True
            await session.commit()
        return {"path": path, "restored": True, "change_id": change.id}

    return Operation(run=run, kind=Kind.WRITE, summary=f"roll back change to {path}",
                     preview=make_diff(path, current, before), state=f"{path}|{sha(current)}")


async def restore_snapshot(ctx: Context, snapshot: Snapshot) -> Operation:
    path = await ctx.resolve(snapshot.path, write=True)
    if path != snapshot.path:
        raise Conflict("the path now resolves elsewhere; restore refused")
    for rule in ctx.grant.paths:
        if rule.mode == "ro" and is_within(rule.path, path):
            raise PolicyDenied(f"{path} contains the read-only path {rule.path}")

    async def run() -> dict:
        parent, name = posixpath.dirname(path), posixpath.basename(path)
        stamp = new_id()[:8]
        staging = posixpath.join(parent, f".atb-restore-{stamp}")
        aside = f"{path}.atb-before-{stamp}"
        q = shlex.quote
        # Extract next to the target, then swap. The replaced tree is kept, never deleted.
        script = (
            f"set -e; mkdir -m 700 {q(staging)}; tar -C {q(staging)} -xzf {q(snapshot.archive)}; "
            f"if [ -e {q(path)} ]; then mv -- {q(path)} {q(aside)}; fi; "
            f"mv -- {q(posixpath.join(staging, name))} {q(path)}; rmdir {q(staging)}"
        )
        result = await ctx.run(["sh", "-c", script], timeout=175)
        if result.exit_code != 0:
            raise RemoteError(f"restore failed: {result.stderr.strip()[:500]}")
        return {"path": path, "restored": True, "snapshot_id": snapshot.id, "previous_content_kept_at": aside}

    return Operation(
        run=run, kind=Kind.WRITE, summary=f"restore snapshot of {path}",
        preview=f"replace {path} with snapshot {snapshot.id} ({snapshot.label or 'no label'})",
        destructive="replaces a whole directory", state=snapshot.id,
    )
