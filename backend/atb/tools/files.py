"""File tools. All of them go through SFTP with canonical-path policy checks."""

import base64
import binascii
import difflib
import hashlib
import posixpath
import shlex

from pydantic import Field

from ..db import Change, new_id
from ..errors import Conflict, NotFound, PolicyDenied, RemoteError, ValidationFailed
from ..policy import Kind, Level, check_path, is_denied_path, is_within
from ..redact import MARKER, redact
from ..ssh.files import MAX_FILE_BYTES, is_dir, is_regular, read_bytes, sftp_session, stat_or_none, write_atomic
from .base import Context, Operation, ServerArgs, tool

PREVIEW_LIMIT = 40_000


def sha(data: bytes | None) -> str:
    return "" if data is None else hashlib.sha256(data).hexdigest()


def decode_text(data: bytes, path: str) -> str:
    if b"\0" in data:
        raise ValidationFailed(f"{path} is a binary file; use download_file")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationFailed(f"{path} is not UTF-8 text; use download_file") from exc


def make_diff(path: str, before: bytes | None, after: bytes | None) -> str:
    try:
        old = (before or b"").decode("utf-8")
        new = (after or b"").decode("utf-8")
    except UnicodeDecodeError:
        return f"binary change to {path}: {len(before or b'')} -> {len(after or b'')} bytes"
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        f"a{path}" if before is not None else "/dev/null", f"b{path}" if after is not None else "/dev/null",
    )
    text = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
    if len(text) > PREVIEW_LIMIT:
        text = text[:PREVIEW_LIMIT] + "\n... diff truncated; the full change is larger than shown ..."
    return text or f"no content change to {path}"


async def record_change(ctx: Context, path: str, operation: str, before: bytes | None, mode: int, after: bytes | None) -> str:
    change_id = new_id()
    encoded = "" if before is None else ctx.services.box.encrypt(base64.b64encode(before).decode(), f"change:{change_id}")
    async with ctx.services.db.session() as session:
        session.add(
            Change(
                id=change_id, actor_id=ctx.actor.id, server_id=ctx.server.id, path=path, operation=operation,
                existed=before is not None, before_enc=encoded, mode=mode, after_sha=sha(after),
            )
        )
        await session.commit()
    return change_id


async def load_current(ctx: Context, path: str) -> tuple[str, bytes | None, int]:
    """Resolve a path for writing and return (path, current content or None, mode)."""
    async with sftp_session(await ctx.connection()) as sftp:
        resolved = await ctx.resolve(path, write=True, sftp=sftp)
        attrs = await stat_or_none(sftp, resolved)
        if attrs is None:
            return resolved, None, 0o644
        return resolved, await read_bytes(sftp, resolved), attrs.permissions & 0o7777


def write_operation(ctx: Context, path: str, before: bytes | None, after: bytes, mode: int, create_dirs: bool) -> Operation:
    async def run() -> dict:
        async with sftp_session(await ctx.connection()) as sftp:
            current = await read_bytes(sftp, path) if await sftp.exists(path) else None
            if sha(current) != sha(before):
                raise Conflict(f"{path} changed while the request was being processed; read it again")
            parent = posixpath.dirname(path)
            if not await sftp.isdir(parent):
                if not create_dirs:
                    raise NotFound(f"directory {parent} does not exist")
                await sftp.makedirs(parent, exist_ok=True)
            change_id = await record_change(ctx, path, "write", before, mode, after)
            await write_atomic(sftp, path, after, mode)
        return {"path": path, "bytes": len(after), "created": before is None, "change_id": change_id, "sha256": sha(after)}

    return Operation(
        run=run, kind=Kind.WRITE, summary=f"{'create' if before is None else 'modify'} {path}",
        preview=make_diff(path, before, after), state=f"{path}|{sha(before)}",
    )


def reject_marker(ctx: Context, text: str) -> None:
    if ctx.actor.is_agent and MARKER in text:
        raise ValidationFailed(
            "the new content contains a redaction placeholder; writing it would destroy the real secret. "
            "Use edit_file on the lines around it instead."
        )


class ReadArgs(ServerArgs):
    path: str
    start_line: int = Field(1, ge=1)
    max_lines: int = Field(2000, ge=1, le=20000)


@tool("read_file", ReadArgs, "Read a UTF-8 text file, optionally a line range.", read_only=True)
async def read_file(ctx: Context, args: ReadArgs) -> Operation:
    path = await ctx.resolve(args.path)

    async def run() -> dict:
        async with sftp_session(await ctx.connection()) as sftp:
            data = await read_bytes(sftp, path)
        text = decode_text(data, path)
        if ctx.actor.is_agent:
            # Redact before slicing so a multi-line secret cannot be read in pieces.
            text = redact(text)
        lines = text.splitlines(keepends=True)
        chunk = lines[args.start_line - 1 : args.start_line - 1 + args.max_lines]
        return {
            "path": path, "content": "".join(chunk), "total_lines": len(lines), "start_line": args.start_line,
            "truncated": args.start_line - 1 + len(chunk) < len(lines), "size": len(data), "sha256": sha(data),
        }

    return Operation(run=run, summary=f"read {path}")


class WriteArgs(ServerArgs):
    path: str
    content: str = Field(max_length=MAX_FILE_BYTES)
    create_directories: bool = False
    expected_sha256: str | None = Field(None, description="Fail unless the current file has this hash")


@tool("write_file", WriteArgs, "Create or overwrite a text file. The previous content can be restored with rollback_changes.")
async def write_file(ctx: Context, args: WriteArgs) -> Operation:
    reject_marker(ctx, args.content)
    path, before, mode = await load_current(ctx, args.path)
    if args.expected_sha256 is not None and sha(before) != args.expected_sha256:
        raise Conflict("the file does not match expected_sha256; read it again")
    return write_operation(ctx, path, before, args.content.encode(), mode, args.create_directories)


class EditArgs(ServerArgs):
    path: str
    old_text: str = Field(min_length=1, description="Exact text to replace")
    new_text: str
    replace_all: bool = False


@tool("edit_file", EditArgs, "Replace an exact piece of text in a file. old_text must be unique unless replace_all is set.")
async def edit_file(ctx: Context, args: EditArgs) -> Operation:
    reject_marker(ctx, args.new_text)
    path, before, mode = await load_current(ctx, args.path)
    if before is None:
        raise NotFound(f"{path} does not exist")
    text = decode_text(before, path)
    count = text.count(args.old_text)
    if count == 0:
        raise ValidationFailed("old_text was not found in the file")
    if count > 1 and not args.replace_all:
        raise ValidationFailed(f"old_text occurs {count} times; add context or set replace_all")
    after = text.replace(args.old_text, args.new_text, -1 if args.replace_all else 1).encode()
    if len(after) > MAX_FILE_BYTES:
        raise ValidationFailed("the edited file would exceed the size limit")
    return write_operation(ctx, path, before, after, mode, False)


class ListArgs(ServerArgs):
    path: str = "."


@tool("list_directory", ListArgs, "List the entries of a directory.", read_only=True)
async def list_directory(ctx: Context, args: ListArgs) -> Operation:
    path = await ctx.resolve(args.path)

    async def run() -> dict:
        entries = []
        async with sftp_session(await ctx.connection()) as sftp:
            if not await sftp.isdir(path):
                raise ValidationFailed(f"{path} is not a directory")
            async for item in sftp.scandir(path):
                name = str(item.filename)
                if name in (".", "..") or is_denied_path(posixpath.join(path, name), ctx.grant):
                    continue
                attrs = item.attrs
                kind = "directory" if is_dir(attrs) else "file" if is_regular(attrs) else "other"
                if attrs.type == 3:  # SSH_FILEXFER_TYPE_SYMLINK
                    kind = "symlink"
                entries.append(
                    {"name": name, "type": kind, "size": attrs.size, "mtime": attrs.mtime,
                     "mode": oct((attrs.permissions or 0) & 0o7777)}
                )
                if len(entries) >= 2000:
                    break
        entries.sort(key=lambda e: (e["type"] != "directory", e["name"]))
        return {"path": path, "entries": entries, "truncated": len(entries) >= 2000}

    return Operation(run=run, summary=f"list {path}")


class SearchArgs(ServerArgs):
    pattern: str = Field(min_length=1, max_length=500, description="Regular expression (content) or glob (name)")
    path: str = "."
    mode: str = Field("content", pattern="^(content|name)$")
    max_results: int = Field(100, ge=1, le=1000)


@tool("search_files", SearchArgs, "Search file contents (grep -E) or file names under a directory.", read_only=True)
async def search_files(ctx: Context, args: SearchArgs) -> Operation:
    path = await ctx.resolve(args.path)

    async def run() -> dict:
        skip = [".git", "node_modules", "__pycache__", ".venv", ".atb", ".ssh"]
        if args.mode == "content":
            argv = ["grep", "-rIHnZ", "-E", *[f"--exclude-dir={name}" for name in skip], "-e", args.pattern, "--", path]
        else:
            prune: list[str] = []
            for name in skip:
                prune += ["-name", name, "-o"]
            argv = ["find", path, "(", *prune[:-1], ")", "-prune", "-o", "-name", args.pattern, "-print0"]
        # Bound the work and the bytes returned, whatever the size of the tree.
        command = f"timeout 30 {shlex.join(argv)} | head -c 400000"
        result = await ctx.run(["bash", "-c", command + '; exit "${PIPESTATUS[0]}"'], timeout=45)
        if result.exit_code == 124:
            raise RemoteError("search timed out; narrow the path")
        if result.exit_code == 2 and not result.stdout:
            raise RemoteError(f"search failed: {result.stderr.strip()[:500]}")
        matches = []
        if args.mode == "content":
            for line in result.stdout.split("\n"):
                file, sep, rest = line.partition("\0")
                number, colon, text = rest.partition(":")
                if sep and colon and number.isdigit() and not is_denied_path(file, ctx.grant):
                    matches.append({"path": file, "line": int(number), "text": text[:500]})
        else:
            matches = [{"path": f} for f in result.stdout.split("\0") if f and not is_denied_path(f, ctx.grant)]
        return {"matches": matches[: args.max_results], "truncated": len(matches) > args.max_results}

    return Operation(run=run, summary=f"search {path} for {args.pattern!r}")


class MkdirArgs(ServerArgs):
    path: str


@tool("create_directory", MkdirArgs, "Create a directory, including missing parents.")
async def create_directory(ctx: Context, args: MkdirArgs) -> Operation:
    path = await ctx.resolve(args.path, write=True)

    async def run() -> dict:
        async with sftp_session(await ctx.connection()) as sftp:
            existed = await sftp.isdir(path)
            if not existed:
                if await sftp.lexists(path):
                    raise Conflict(f"{path} exists and is not a directory")
                await sftp.makedirs(path, exist_ok=True)
        return {"path": path, "created": not existed}

    return Operation(run=run, kind=Kind.WRITE, summary=f"create directory {path}", state=path)


class DeleteArgs(ServerArgs):
    path: str
    recursive: bool = Field(False, description="Required to delete a non-empty directory; cannot be rolled back")


def guard_tree(ctx: Context, path: str) -> None:
    """Refuse whole-tree operations that would reach protected or read-only areas."""
    if path == "/" or any(path == rule.path for rule in ctx.grant.paths):
        raise PolicyDenied("a granted root directory cannot be deleted or replaced")
    for rule in ctx.grant.paths:
        if rule.mode == "ro" and is_within(rule.path, path):
            raise PolicyDenied(f"{path} contains the read-only path {rule.path}")


@tool("delete_file", DeleteArgs, "Delete a file or directory. Single files can be restored with rollback_changes.")
async def delete_file(ctx: Context, args: DeleteArgs) -> Operation:
    async with sftp_session(await ctx.connection()) as sftp:
        home = str(await sftp.realpath("."))
        raw = args.path if args.path.startswith("/") else posixpath.join(ctx.default_dir(home), args.path)
        raw = posixpath.normpath(raw)
        # Resolve the parent only: deleting a symlink must remove the link, not its target.
        parent = await ctx.resolve(posixpath.dirname(raw), sftp=sftp)
        path = posixpath.join(parent, posixpath.basename(raw))
        grant = await ctx.resolved_grant(sftp)
        check_path(path, grant, write=True)
        ctx.grant = grant
        guard_tree(ctx, path)
        if not await sftp.lexists(path):
            raise NotFound(f"{path} does not exist")
        directory = await sftp.isdir(path) and not await sftp.islink(path)
        link = await sftp.islink(path)
        before, mode = None, 0o644
        if not directory and not link:
            attrs = await sftp.stat(path)
            mode = attrs.permissions & 0o7777
            if is_regular(attrs) and attrs.size <= MAX_FILE_BYTES:
                before = await read_bytes(sftp, path)
    if directory and ctx.grant.level != Level.OWNER and args.recursive:
        found = await ctx.run(["find", path, "-name", ".ssh", "-o", "-name", ".atb", "-o", "-name", "id_rsa*"])
        if found.stdout.strip():
            raise PolicyDenied("the directory contains protected files")
    if directory and args.recursive:
        destructive = "recursively deletes a directory (cannot be rolled back)"
        preview = f"rm -rf -- {path}"
    elif before is not None:
        destructive, preview = "", make_diff(path, before, None)
    elif directory:
        destructive, preview = "", f"rmdir {path}"
    elif link:
        destructive, preview = "", f"remove the symbolic link {path} (its target is not touched)"
    else:
        destructive, preview = "deletes a file that is too large or special to back up", f"rm -- {path}"

    async def run() -> dict:
        async with sftp_session(await ctx.connection()) as sftp:
            if directory and not args.recursive:
                if [n for n in await sftp.listdir(path) if n not in (".", "..")]:
                    raise Conflict("directory is not empty; set recursive to true")
                await sftp.rmdir(path)
                return {"path": path, "deleted": True, "recoverable": False}
            if not directory:
                change_id = None
                if before is not None:
                    if sha(await read_bytes(sftp, path)) != sha(before):
                        raise Conflict("the file changed while the request was being processed")
                    change_id = await record_change(ctx, path, "delete", before, mode, None)
                await sftp.remove(path)
                return {"path": path, "deleted": True, "recoverable": change_id is not None, "change_id": change_id}
        result = await ctx.run(["rm", "-rf", "--", path], timeout=120)
        if result.exit_code != 0:
            raise RemoteError(result.stderr.strip()[:500])
        return {"path": path, "deleted": True, "recoverable": False}

    return Operation(
        run=run, kind=Kind.WRITE, summary=f"delete {path}", preview=preview, destructive=destructive,
        state=f"{path}|{sha(before)}",
    )


class UploadArgs(ServerArgs):
    path: str
    content_base64: str = Field(max_length=MAX_FILE_BYTES * 4 // 3 + 8)
    overwrite: bool = False
    create_directories: bool = False


@tool("upload_file", UploadArgs, "Upload binary content (base64) to a file on the server. Limit 5 MiB.")
async def upload_file(ctx: Context, args: UploadArgs) -> Operation:
    try:
        data = base64.b64decode(args.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationFailed("content_base64 is not valid base64") from exc
    if len(data) > MAX_FILE_BYTES:
        raise ValidationFailed("file exceeds the 5 MiB limit")
    reject_marker(ctx, data.decode("utf-8", errors="ignore"))
    path, before, mode = await load_current(ctx, args.path)
    if before is not None and not args.overwrite:
        raise Conflict(f"{path} already exists; set overwrite to true")
    return write_operation(ctx, path, before, data, mode, args.create_directories)


class DownloadArgs(ServerArgs):
    path: str


@tool("download_file", DownloadArgs, "Download a file as base64. Limit 5 MiB.", read_only=True)
async def download_file(ctx: Context, args: DownloadArgs) -> Operation:
    path = await ctx.resolve(args.path)

    async def run() -> dict:
        async with sftp_session(await ctx.connection()) as sftp:
            data = await read_bytes(sftp, path)
        if ctx.actor.is_agent:
            text = data.decode("utf-8", errors="ignore")
            if redact(text) != text:
                raise PolicyDenied("the file appears to contain secrets; use read_file, which masks them")
        return {"path": path, "size": len(data), "sha256": sha(data), "content_base64": base64.b64encode(data).decode()}

    return Operation(run=run, summary=f"download {path}")


