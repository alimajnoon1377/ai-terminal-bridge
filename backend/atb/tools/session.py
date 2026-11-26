"""Discovery, connection and approval-status tools."""

from pydantic import Field
from sqlalchemy import select

from ..db import Grant, Server, now
from ..errors import NotFound
from ..ssh.client import fingerprint_of
from .base import Args, Context, Operation, ServerArgs, tool
from .execute import sandbox_available


def grant_summary(config: dict) -> dict:
    keys = ("level", "paths", "services", "allowed_commands", "force_sandbox", "sandbox_network", "allow_docker", "max_runtime_seconds")
    return {key: config.get(key) for key in keys}


@tool("list_servers", Args, "List the servers you may use and the access you have on each.", read_only=True)
async def list_servers(ctx: Context, args: Args) -> Operation:
    async def run() -> dict:
        async with ctx.services.db.session() as session:
            if ctx.actor.is_agent:
                rows = await session.execute(
                    select(Server, Grant).join(Grant, Grant.server_id == Server.id).where(Grant.agent_id == ctx.actor.id)
                )
                items = [(server, grant_summary(grant.config)) for server, grant in rows]
            else:
                query = select(Server)
                if not ctx.actor.is_admin:
                    query = query.where(Server.owner_id == ctx.actor.id)
                items = [(server, {"level": "owner"}) for server in await session.scalars(query)]
        return {
            "servers": [
                {
                    "id": server.id,
                    "name": server.name,
                    "connected": ctx.services.pool.is_connected(server.id, ctx.actor.id),
                    "access": access,
                }
                for server, access in items
            ]
        }

    return Operation(run=run, summary="list servers")


@tool("connect_server", ServerArgs, "Open the SSH connection and report the server's capabilities and your scope.", read_only=True)
async def connect_server(ctx: Context, args: ServerArgs) -> Operation:
    async def run() -> dict:
        info = await ctx.run(["sh", "-c", "uname -s; uname -m; id -un; id -u"])
        system, machine, user, uid = (info.stdout.split() + ["", "", "", ""])[:4]
        return {
            "connected": True,
            "server": ctx.server.name,
            "system": system,
            "architecture": machine,
            "user": user,
            "is_root": uid == "0",
            "home": await ctx.home(),
            "host_key_fingerprint": fingerprint_of(ctx.server.host_key),
            "sandbox_available": await sandbox_available(ctx),
            "access": grant_summary(ctx.grant.model_dump(mode="json")),
        }

    return Operation(run=run, summary="connect")


@tool("disconnect_server", ServerArgs, "Close your SSH connection. Running commands keep running on the server.", read_only=True)
async def disconnect_server(ctx: Context, args: ServerArgs) -> Operation:
    async def run() -> dict:
        closed = await ctx.services.pool.close(ctx.server.id, ctx.actor.id)
        still_running = await ctx.services.jobs.running(ctx.actor.id, ctx.server.id)
        return {"disconnected": bool(closed), "commands_still_running": [job.id for job in still_running]}

    return Operation(run=run, summary="disconnect")


class ApprovalArgs(Args):
    approval_id: str
    wait_seconds: float = Field(0, ge=0, le=50, description="Wait this long for a decision")


@tool("get_approval", ApprovalArgs, "Check whether a human has decided on a pending approval request.", read_only=True)
async def get_approval(ctx: Context, args: ApprovalArgs) -> Operation:
    async def run() -> dict:
        approval = await ctx.services.approval_wait(args.approval_id, args.wait_seconds)
        if approval is None or (ctx.actor.is_agent and approval.agent_id != ctx.actor.id):
            raise NotFound("no such approval request")
        status = "expired" if approval.status in ("pending", "approved") and approval.expires_at < now() else approval.status
        result = {"approval_id": approval.id, "status": status, "tool": approval.tool, "summary": approval.summary}
        if status == "approved":
            result["next_step"] = "Call the same tool again with identical arguments to execute it."
        if approval.reason:
            result["reason"] = approval.reason
        return result

    return Operation(run=run, summary="check approval")


