"""SFTP file operations. Every path is canonicalised on the target before policy checks."""

import posixpath
import secrets
import stat as stat_module
from contextlib import asynccontextmanager

import asyncssh

from ..errors import NotFound, RemoteError, ValidationFailed

MAX_FILE_BYTES = 5 * 1024 * 1024


@asynccontextmanager
async def sftp_session(conn: asyncssh.SSHClientConnection):
    """Open an SFTP channel and translate protocol errors into bridge errors."""
    try:
        async with conn.start_sftp_client() as sftp:
            yield sftp
    except asyncssh.SFTPNoSuchFile as exc:
        raise NotFound(f"no such file or directory ({exc.reason})") from exc
    except asyncssh.SFTPPermissionDenied as exc:
        raise RemoteError(f"permission denied by the server's file system ({exc.reason})") from exc
    except asyncssh.SFTPError as exc:
        raise RemoteError(f"SFTP error: {exc.reason}") from exc
    except (OSError, asyncssh.Error) as exc:
        raise RemoteError(f"SSH error: {exc}") from exc


async def canonical(sftp: asyncssh.SFTPClient, path: str, base: str) -> str:
    """Resolve `path` as the target would, including for paths that do not exist yet.

    `..` is collapsed first, then symlinks are resolved. The caller performs the real
    operation on the returned path, so the checked path and the used path are the same.
    """
    if not path or "\0" in path or "\n" in path:
        raise ValidationFailed("invalid path")
    if path == "~" or path.startswith("~/"):
        raise ValidationFailed("use an absolute path instead of ~")
    if not path.startswith("/"):
        path = posixpath.join(base, path)
    current = "/" + posixpath.normpath(path).lstrip("/")
    missing: list[str] = []
    while current != "/" and not await sftp.lexists(current):
        missing.insert(0, posixpath.basename(current))
        current = posixpath.dirname(current)
    if await sftp.islink(current) and not await sftp.exists(current):
        raise ValidationFailed(f"{current} is a dangling symbolic link")
    resolved = str(await sftp.realpath(current))
    return posixpath.join(resolved, *missing) if missing else resolved


async def stat_or_none(sftp: asyncssh.SFTPClient, path: str):
    try:
        return await sftp.stat(path)
    except asyncssh.SFTPNoSuchFile:
        return None


def is_dir(attrs) -> bool:
    return stat_module.S_ISDIR(attrs.permissions or 0)


def is_regular(attrs) -> bool:
    return stat_module.S_ISREG(attrs.permissions or 0)


async def read_bytes(sftp: asyncssh.SFTPClient, path: str, limit: int = MAX_FILE_BYTES) -> bytes:
    attrs = await sftp.stat(path)
    if not is_regular(attrs):
        raise ValidationFailed(f"{path} is not a regular file")
    if (attrs.size or 0) > limit:
        raise ValidationFailed(f"{path} is {attrs.size} bytes; the limit for this operation is {limit}")
    async with sftp.open(path, "rb") as handle:
        data = await handle.read()
    return data if isinstance(data, bytes) else data.encode()


async def write_atomic(sftp: asyncssh.SFTPClient, path: str, data: bytes, mode: int | None = None) -> None:
    """Write to a sibling temporary file and rename it over the destination."""
    existing = await stat_or_none(sftp, path)
    if existing is not None and not is_regular(existing):
        raise ValidationFailed(f"{path} exists and is not a regular file")
    if mode is None:
        mode = stat_module.S_IMODE(existing.permissions) if existing else 0o644
    temp = posixpath.join(posixpath.dirname(path), f".atb-tmp-{secrets.token_hex(8)}")
    try:
        async with sftp.open(temp, "wb", asyncssh.SFTPAttrs(permissions=0o600)) as handle:
            await handle.write(data)
        await sftp.chmod(temp, mode)
        await sftp.posix_rename(temp, path)
    except BaseException:
        if await sftp.lexists(temp):
            await sftp.remove(temp)
        raise
