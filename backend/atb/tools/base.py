"""Shared machinery for tools.

A tool is split in two so that authorisation always happens before side effects:
the registered function validates the request against the grant and returns an
`Operation`; the gateway decides (allow / approve / deny) and only then calls `run`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from ..db import Server
from ..errors import PolicyDenied
from ..policy import GrantConfig, Kind, Level, check_path
from ..ssh.client import Target
from ..ssh.files import canonical, sftp_session


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerArgs(Args):
    server: str = Field(description="Server name or id, as returned by list_servers")


@dataclass
class Operation:
    run: Callable[[], Awaitable[dict]]
    kind: Kind = Kind.READ
    summary: str = ""
    preview: str = ""
    """Shown to the human approver, e.g. the exact command or a unified diff."""
    destructive: str = ""
    sandboxed: bool = False
    state: str = ""
    """Fingerprint of remote state the approval depends on (e.g. a file hash)."""


@dataclass(frozen=True)
class Actor:
    type: str  # "agent" or "user"
    id: str
    name: str
    owner_id: str
    is_admin: bool = False

    @property
    def is_agent(self) -> bool:
        return self.type == "agent"


@dataclass
class Context:
    services: "object"  # atb.services.Services (not imported to avoid a cycle)
    actor: Actor
    server: Server | None = None
    grant: GrantConfig | None = None
    target: Target | None = None

    async def connection(self):
        return await self.services.pool.get(self.target, self.actor.id)

    async def run(self, argv, *, timeout: float = 60, input: str | None = None):
        return await self.services.pool.run(self.target, self.actor.id, argv, timeout=timeout, input=input)

    async def home(self) -> str:
        async with sftp_session(await self.connection()) as sftp:
            return str(await sftp.realpath("."))

    def default_dir(self, home: str) -> str:
        if self.grant.level != Level.OWNER:
            for rule in self.grant.paths:
                if rule.mode == "rw":
                    return rule.path
            if self.grant.paths:
                return self.grant.paths[0].path
        return home

    async def resolve(self, path: str, *, write: bool = False, sftp=None) -> str:
        """Canonicalise a path on the target and enforce the grant on the result."""

        async def inner(client):
            home = str(await client.realpath("."))
            resolved = await canonical(client, path, self.default_dir(home))
            check_path(resolved, await self.resolved_grant(client), write=write)
            return resolved

        if sftp is not None:
            return await inner(sftp)
        async with sftp_session(await self.connection()) as client:
            return await inner(client)

    async def resolved_grant(self, sftp) -> GrantConfig:
        """Return the grant after checking that no granted root is a symbolic link.

        Following such links would let anyone able to replace a granted directory with a
        link (for example from inside a sandbox) widen the grant to the link's target.
        """
        if self.grant.level == Level.OWNER:
            return self.grant
        for rule in self.grant.paths:
            if not await sftp.lexists(rule.path):
                continue
            real = str(await sftp.realpath(rule.path)) if await sftp.exists(rule.path) else None
            if real != rule.path:
                raise PolicyDenied(
                    f"the granted path {rule.path} is or contains a symbolic link; the owner must grant the real path"
                )
        return self.grant

    async def has_program(self, name: str) -> bool:
        return (await self.run(["sh", "-c", f"command -v {name} >/dev/null 2>&1"])).exit_code == 0

    def require_paths(self) -> None:
        if not self.grant.paths:
            raise PolicyDenied("no paths are granted to this assistant on this server")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    model: type[BaseModel]
    handler: Callable[[Context, BaseModel], Awaitable[Operation]]
    read_only: bool
    needs_server: bool

    @property
    def input_schema(self) -> dict:
        schema = self.model.model_json_schema()
        schema.pop("title", None)
        return schema


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, model: type[BaseModel], description: str, *, read_only: bool = False):
    def register(handler):
        if name in REGISTRY:
            raise ValueError(f"tool {name} is already registered")
        REGISTRY[name] = ToolSpec(name, description, model, handler, read_only, "server" in model.model_fields)
        return handler

    return register
