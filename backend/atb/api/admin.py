"""Management API used by the dashboard (human users only)."""

from collections import deque
from typing import Literal

import asyncssh
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from ..crypto import hash_password, hash_token, new_token, verify_password
from ..db import Agent, Approval, AuditEntry, Change, Grant, Job, LoginSession, Server, Snapshot, User, new_id, now
from ..errors import AuthFailed, Conflict, NotFound, PolicyDenied, RateLimited, ValidationFailed
from ..policy import GrantConfig
from ..services import Services, user_actor
from ..ssh.client import fetch_host_key, fingerprint_of, generate_keypair, public_key_from_private
from .deps import admin_user, bearer, current_user, get_services

router = APIRouter(prefix="/api")

_failures: dict[str, deque] = {}
_MAX_FAILURES, _WINDOW = 8, 300


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def columns(row, *names) -> dict:
    return {name: getattr(row, name) for name in names}


def user_view(user: User) -> dict:
    return columns(user, "id", "username", "role", "disabled", "created_at")


def server_view(server: Server) -> dict:
    view = columns(server, "id", "owner_id", "name", "host", "port", "username", "auth_type", "public_key", "created_at")
    view["host_key_fingerprint"] = fingerprint_of(server.host_key)
    view["trusted"] = bool(server.host_key)
    return view


def agent_view(agent: Agent) -> dict:
    return columns(agent, "id", "owner_id", "name", "kind", "token_hint", "paused", "rate_limit_per_minute",
                   "created_at", "last_seen_at")


async def owned(services: Services, model, record_id: str, user: User):
    async with services.db.session() as session:
        record = await session.get(model, record_id)
    if record is None or (user.role != "admin" and record.owner_id != user.id):
        raise NotFound(f"no such {model.__name__.lower()}")
    return record


async def log_admin(services: Services, user: User, action: str, detail: dict, server_id: str = "", owner_id: str = ""):
    await services.audit.record(
        actor_type="user", actor_id=user.id, actor_name=user.username, owner_id=owner_id or user.id,
        server_id=server_id, action=action, status="ok", detail=detail,
    )


# --- authentication ---------------------------------------------------------


class LoginBody(Body):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, services: Services = Depends(get_services)):
    key = f"{request.client.host if request.client else '?'}|{body.username.lower()}"
    if len(_failures) > 10_000:
        _failures.clear()
    failures = _failures.setdefault(key, deque())
    while failures and failures[0] < now() - _WINDOW:
        failures.popleft()
    if len(failures) >= _MAX_FAILURES:
        raise RateLimited("too many failed sign-in attempts; try again in a few minutes")
    async with services.db.session() as session:
        user = await session.scalar(select(User).where(User.username == body.username))
        valid = verify_password(body.password, user.password_hash if user else None)
        if not valid or user.disabled:
            failures.append(now())
            raise AuthFailed("wrong username or password")
        token = new_token("atbs")
        await session.execute(delete(LoginSession).where(LoginSession.expires_at < now()))
        session.add(LoginSession(token_hash=hash_token(token), user_id=user.id,
                                 expires_at=now() + services.settings.session_hours * 3600))
        await session.commit()
    await log_admin(services, user, "login", {})
    return {"token": token, "user": user_view(user)}


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, user: User = Depends(current_user), services: Services = Depends(get_services)):
    async with services.db.session() as session:
        await session.execute(delete(LoginSession).where(
            LoginSession.token_hash == hash_token(bearer(request.headers.get("authorization")))))
        await session.commit()
    return Response(status_code=204)


@router.get("/auth/me")
async def me(user: User = Depends(current_user)):
    return user_view(user)


class PasswordBody(Body):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


@router.post("/auth/password", status_code=204)
async def change_password(body: PasswordBody, user: User = Depends(current_user), services: Services = Depends(get_services)):
    if not verify_password(body.current_password, user.password_hash):
        raise PolicyDenied("the current password is wrong")
    async with services.db.session() as session:
        row = await session.get(User, user.id)
        row.password_hash = hash_password(body.new_password)
        await session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        await session.commit()
    await log_admin(services, user, "password_change", {})
    return Response(status_code=204)


# --- users ------------------------------------------------------------------


class UserBody(Body):
    username: str = Field(pattern=r"^[A-Za-z0-9_.\-]{2,64}$")
    password: str = Field(min_length=10, max_length=256)
    role: Literal["admin", "user"] = "user"


class UserPatch(Body):
    disabled: bool | None = None
    role: Literal["admin", "user"] | None = None
    password: str | None = Field(None, min_length=10, max_length=256)


@router.get("/users")
async def list_users(_: User = Depends(admin_user), services: Services = Depends(get_services)):
    async with services.db.session() as session:
        return [user_view(u) for u in await session.scalars(select(User).order_by(User.username))]


@router.post("/users", status_code=201)
async def add_user(body: UserBody, admin: User = Depends(admin_user), services: Services = Depends(get_services)):
    user = await services.create_user(body.username, body.password, body.role)
    await log_admin(services, admin, "user_create", {"username": user.username, "role": user.role})
    return user_view(user)


@router.patch("/users/{user_id}")
async def patch_user(user_id: str, body: UserPatch, admin: User = Depends(admin_user),
                     services: Services = Depends(get_services)):
    if user_id == admin.id and (body.disabled or body.role == "user"):
        raise Conflict("you cannot disable or demote your own account")
    async with services.db.session() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise NotFound("no such user")
        if body.disabled is not None:
            user.disabled = body.disabled
        if body.role is not None:
            user.role = body.role
        if body.password is not None:
            user.password_hash = hash_password(body.password)
        if body.disabled or body.password is not None:
            await session.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
        await session.commit()
        agents = list(await session.scalars(select(Agent.id).where(Agent.owner_id == user_id)))
    if body.disabled:
        for agent_id in agents:
            await services.stop_jobs(agent_id)
    await log_admin(services, admin, "user_update", {"user_id": user_id, "changed": sorted(body.model_fields_set)})
    return user_view(user)


# --- servers ----------------------------------------------------------------


class ServerBody(Body):
    name: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")
    host: str = Field(pattern=r"^[A-Za-z0-9_.:\-]{1,255}$")
    port: int = Field(22, ge=1, le=65535)
    username: str = Field(pattern=r"^[A-Za-z0-9_.\-\\@]{1,64}$")
    auth_type: Literal["key", "password", "generate"] = "generate"
    secret: str = Field("", max_length=20000, description="Private key or password; empty when generating a key")


def prepare_secret(body: ServerBody) -> tuple[str, str, str]:
    """Return (auth_type, secret, public_key)."""
    if body.auth_type == "generate":
        private, public = generate_keypair(f"atb-{body.name}")
        return "key", private, public
    if not body.secret:
        raise ValidationFailed("a private key or password is required")
    if body.auth_type == "key":
        secret = body.secret.replace("\r\n", "\n").strip() + "\n"
        try:
            return "key", secret, public_key_from_private(secret)
        except (asyncssh.KeyImportError, ValueError) as exc:
            raise ValidationFailed(f"cannot read the private key (passphrase-protected keys are not supported): {exc}") from exc
    return "password", body.secret, ""


@router.get("/servers")
async def list_servers(user: User = Depends(current_user), services: Services = Depends(get_services)):
    query = select(Server).order_by(Server.name)
    if user.role != "admin":
        query = query.where(Server.owner_id == user.id)
    async with services.db.session() as session:
        return [server_view(s) for s in await session.scalars(query)]


@router.post("/servers", status_code=201)
async def add_server(body: ServerBody, user: User = Depends(current_user), services: Services = Depends(get_services)):
    auth_type, secret, public = prepare_secret(body)
    server_id = new_id()
    async with services.db.session() as session:
        if await session.scalar(select(Server).where(Server.owner_id == user.id, Server.name == body.name)):
            raise Conflict("you already have a server with that name")
        server = Server(
            id=server_id, owner_id=user.id, name=body.name, host=body.host, port=body.port, username=body.username,
            auth_type=auth_type, secret_enc=services.box.encrypt(secret, f"server:{server_id}"), public_key=public,
        )
        session.add(server)
        await session.commit()
    await log_admin(services, user, "server_create", {"name": body.name, "host": body.host}, server_id)
    return server_view(server)


@router.delete("/servers/{server_id}", status_code=204)
async def remove_server(server_id: str, user: User = Depends(current_user), services: Services = Depends(get_services)):
    server = await owned(services, Server, server_id, user)
    if failed := await services.stop_jobs(server_id=server_id):
        raise Conflict(f"could not stop running commands: {', '.join(failed)}")
    await services.pool.close(server_id)
    async with services.db.session() as session:
        await session.execute(delete(Server).where(Server.id == server_id))
        await session.commit()
    await log_admin(services, user, "server_delete", {"name": server.name}, server_id, server.owner_id)
    return Response(status_code=204)


@router.post("/servers/{server_id}/probe")
async def probe_host_key(server_id: str, user: User = Depends(current_user), services: Services = Depends(get_services)):
    server = await owned(services, Server, server_id, user)
    found = await fetch_host_key(server.host, server.port, services.settings.connect_timeout)
    return {"fingerprint": found["fingerprint"], "algorithm": found["algorithm"],
            "pinned_fingerprint": fingerprint_of(server.host_key)}


class TrustBody(Body):
    fingerprint: str


@router.post("/servers/{server_id}/trust")
async def trust_host_key(server_id: str, body: TrustBody, user: User = Depends(current_user),
                         services: Services = Depends(get_services)):
    server = await owned(services, Server, server_id, user)
    found = await fetch_host_key(server.host, server.port, services.settings.connect_timeout)
    if found["fingerprint"] != body.fingerprint:
        raise Conflict("the server's key differs from the fingerprint you confirmed")
    async with services.db.session() as session:
        row = await session.get(Server, server_id)
        row.host_key = found["host_key"]
        await session.commit()
    await services.pool.close(server_id)
    await log_admin(services, user, "host_key_trust", {"fingerprint": body.fingerprint}, server_id, server.owner_id)
    return server_view(row)


@router.get("/servers/{server_id}/history")
async def server_history(server_id: str, user: User = Depends(current_user), services: Services = Depends(get_services)):
    await owned(services, Server, server_id, user)
    async with services.db.session() as session:
        changes = await session.scalars(
            select(Change).where(Change.server_id == server_id).order_by(Change.created_at.desc()).limit(100))
        change_list = [columns(c, "id", "path", "operation", "rolled_back", "created_at") for c in changes]
        snapshots = await session.scalars(
            select(Snapshot).where(Snapshot.server_id == server_id).order_by(Snapshot.created_at.desc()).limit(100))
        snapshot_list = [columns(s, "id", "path", "label", "created_at") for s in snapshots]
    return {"changes": change_list, "snapshots": snapshot_list}


# --- assistants and grants ----------------------------------------------------


class AgentBody(Body):
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field("generic", max_length=32)
    rate_limit_per_minute: int = Field(120, ge=1, le=6000)


class AgentPatch(Body):
    name: str | None = Field(None, min_length=1, max_length=64)
    paused: bool | None = None
    rate_limit_per_minute: int | None = Field(None, ge=1, le=6000)


@router.get("/agents")
async def list_agents(user: User = Depends(current_user), services: Services = Depends(get_services)):
    query = select(Agent).order_by(Agent.name)
    if user.role != "admin":
        query = query.where(Agent.owner_id == user.id)
    async with services.db.session() as session:
        return [agent_view(a) for a in await session.scalars(query)]


@router.post("/agents", status_code=201)
async def add_agent(body: AgentBody, user: User = Depends(current_user), services: Services = Depends(get_services)):
    token = new_token("atbag")
    async with services.db.session() as session:
        agent = Agent(owner_id=user.id, token_hash=hash_token(token), token_hint=token[-4:], **body.model_dump())
        session.add(agent)
        await session.commit()
    await log_admin(services, user, "agent_create", {"name": body.name})
    return {**agent_view(agent), "token": token}


@router.post("/agents/{agent_id}/rotate")
async def rotate_token(agent_id: str, user: User = Depends(current_user), services: Services = Depends(get_services)):
    await owned(services, Agent, agent_id, user)
    token = new_token("atbag")
    async with services.db.session() as session:
        agent = await session.get(Agent, agent_id)
        agent.token_hash, agent.token_hint = hash_token(token), token[-4:]
        await session.commit()
    await log_admin(services, user, "agent_token_rotate", {"agent_id": agent_id}, owner_id=agent.owner_id)
    return {**agent_view(agent), "token": token}


@router.patch("/agents/{agent_id}")
async def patch_agent(agent_id: str, body: AgentPatch, user: User = Depends(current_user),
                      services: Services = Depends(get_services)):
    await owned(services, Agent, agent_id, user)
    async with services.db.session() as session:
        agent = await session.get(Agent, agent_id)
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(agent, key, value)
        await session.commit()
    not_stopped = await services.stop_jobs(agent_id) if body.paused else []
    await log_admin(services, user, "agent_update", {"agent_id": agent_id, **body.model_dump(exclude_none=True)},
                    owner_id=agent.owner_id)
    return {**agent_view(agent), "not_stopped": not_stopped}


@router.delete("/agents/{agent_id}", status_code=204)
async def remove_agent(agent_id: str, user: User = Depends(current_user), services: Services = Depends(get_services)):
    agent = await owned(services, Agent, agent_id, user)
    if failed := await services.stop_jobs(agent_id):
        raise Conflict(f"could not stop running commands: {', '.join(failed)}")
    async with services.db.session() as session:
        await session.execute(delete(Agent).where(Agent.id == agent_id))
        await session.commit()
    await log_admin(services, user, "agent_delete", {"name": agent.name}, owner_id=agent.owner_id)
    return Response(status_code=204)


@router.get("/agents/{agent_id}/grants")
async def list_grants(agent_id: str, user: User = Depends(current_user), services: Services = Depends(get_services)):
    await owned(services, Agent, agent_id, user)
    async with services.db.session() as session:
        rows = await session.scalars(select(Grant).where(Grant.agent_id == agent_id))
        return [{"server_id": g.server_id, "config": g.config} for g in rows]


@router.put("/agents/{agent_id}/grants/{server_id}")
async def put_grant(agent_id: str, server_id: str, body: GrantConfig, user: User = Depends(current_user),
                    services: Services = Depends(get_services)):
    await owned(services, Agent, agent_id, user)
    server = await owned(services, Server, server_id, user)
    async with services.db.session() as session:
        grant = await session.scalar(select(Grant).where(Grant.agent_id == agent_id, Grant.server_id == server_id))
        config = body.model_dump(mode="json")
        if grant is None:
            session.add(Grant(agent_id=agent_id, server_id=server_id, config=config))
        else:
            grant.config = config
        await session.commit()
    # Work started under the old permissions must not outlive them.
    not_stopped = await services.stop_jobs(agent_id, server_id)
    await log_admin(services, user, "grant_set", {"agent_id": agent_id, "config": config}, server_id, server.owner_id)
    return {"server_id": server_id, "config": config, "not_stopped": not_stopped}


@router.delete("/agents/{agent_id}/grants/{server_id}", status_code=204)
async def remove_grant(agent_id: str, server_id: str, user: User = Depends(current_user),
                       services: Services = Depends(get_services)):
    await owned(services, Agent, agent_id, user)
    server = await owned(services, Server, server_id, user)
    if failed := await services.stop_jobs(agent_id, server_id):
        raise Conflict(f"could not stop running commands: {', '.join(failed)}")
    async with services.db.session() as session:
        await session.execute(delete(Grant).where(Grant.agent_id == agent_id, Grant.server_id == server_id))
        await session.commit()
    await services.pool.close(server_id, agent_id)
    await log_admin(services, user, "grant_delete", {"agent_id": agent_id}, server_id, server.owner_id)
    return Response(status_code=204)


# --- approvals, jobs, audit, emergency stop ----------------------------------


@router.get("/approvals")
async def list_approvals(status: str = "", user: User = Depends(current_user), services: Services = Depends(get_services)):
    query = (select(Approval, Agent.name, Server.name)
             .join(Agent, Agent.id == Approval.agent_id).join(Server, Server.id == Approval.server_id)
             .order_by(Approval.created_at.desc()).limit(200))
    if user.role != "admin":
        query = query.where(Server.owner_id == user.id)
    if status:
        query = query.where(Approval.status == status)
    if status == "pending":
        query = query.where(Approval.expires_at > now())
    async with services.db.session() as session:
        return [
            {**columns(a, "id", "agent_id", "server_id", "tool", "summary", "status", "reason",
                       "created_at", "expires_at", "decided_by"), "agent_name": agent, "server_name": server,
             "preview": services.box.decrypt(a.preview, f"approval:{a.id}")}
            for a, agent, server in await session.execute(query)
        ]


class DecisionBody(Body):
    approve: bool
    reason: str = Field("", max_length=1000)


@router.post("/approvals/{approval_id}/decision")
async def decide(approval_id: str, body: DecisionBody, user: User = Depends(current_user),
                 services: Services = Depends(get_services)):
    approval = await services.decide_approval(approval_id, user, body.approve, body.reason)
    return {"id": approval.id, "status": approval.status}


@router.get("/jobs")
async def list_jobs(limit: int = 100, user: User = Depends(current_user), services: Services = Depends(get_services)):
    query = (select(Job, Server.name).join(Server, Server.id == Job.server_id)
             .order_by(Job.created_at.desc()).limit(min(max(limit, 1), 500)))
    if user.role != "admin":
        query = query.where(Server.owner_id == user.id)
    async with services.db.session() as session:
        rows = list(await session.execute(query))
        names = dict(list(await session.execute(select(Agent.id, Agent.name)))
                     + list(await session.execute(select(User.id, User.username))))
    return [
        {**columns(job, "id", "actor_type", "actor_id", "server_id", "cwd", "status", "exit_code",
                   "sandboxed", "created_at", "finished_at"),
         "command": services.box.decrypt(job.command, f"job:{job.id}"),
         "server_name": server, "actor_name": names.get(job.actor_id, "?")}
        for job, server in rows
    ]


@router.post("/jobs/{job_id}/terminate")
async def terminate_job(job_id: str, user: User = Depends(current_user), services: Services = Depends(get_services)):
    return await services.gateway.call(user_actor(user), "terminate_command", {"command_id": job_id})


@router.get("/audit")
async def audit_log(limit: int = 100, before: int | None = None, actor_id: str = "", server_id: str = "",
                    user: User = Depends(current_user), services: Services = Depends(get_services)):
    query = select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(min(max(limit, 1), 500))
    if user.role != "admin":
        query = query.where(AuditEntry.owner_id == user.id)
    if before:
        query = query.where(AuditEntry.seq < before)
    if actor_id:
        query = query.where(AuditEntry.actor_id == actor_id)
    if server_id:
        query = query.where(AuditEntry.server_id == server_id)
    async with services.db.session() as session:
        return [columns(e, "seq", "ts", "actor_type", "actor_name", "server_id", "action", "status", "detail", "hash")
                for e in await session.scalars(query)]


@router.get("/audit/verify")
async def audit_verify(_: User = Depends(admin_user), services: Services = Depends(get_services)):
    return await services.audit.verify()


@router.get("/settings/kill-switch")
async def kill_switch_state(_: User = Depends(current_user), services: Services = Depends(get_services)):
    return {"enabled": await services.kill_switch()}


class KillBody(Body):
    enabled: bool


@router.put("/settings/kill-switch")
async def set_kill_switch(body: KillBody, admin: User = Depends(admin_user), services: Services = Depends(get_services)):
    not_stopped = await services.set_kill_switch(body.enabled)
    await log_admin(services, admin, "kill_switch", {"enabled": body.enabled, "not_stopped": not_stopped})
    services.events.publish("kill_switch", admin.id, enabled=body.enabled)
    return {"enabled": body.enabled, "not_stopped": not_stopped}
