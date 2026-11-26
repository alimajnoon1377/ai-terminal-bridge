"""Application container: wires storage, SSH, jobs, approvals and the gateway together."""

import asyncio
import logging

from sqlalchemy import func, select, update

from .audit import AuditLog
from .config import Settings
from .crypto import SecretBox, hash_password, load_master_key
from .db import Agent, Approval, Database, Grant, Server, Setting, User, now
from .errors import Conflict, NotFound, PolicyDenied
from .events import EventBus
from .gateway import Gateway
from .policy import GrantConfig, owner_grant
from .ssh.client import SSHPool, Target
from .ssh.jobs import JobManager
from .tools.base import Actor, Context

log = logging.getLogger(__name__)


def user_actor(user: User) -> Actor:
    return Actor("user", user.id, user.username, user.id, user.role == "admin")


def agent_actor(agent: Agent) -> Actor:
    return Actor("agent", agent.id, agent.name, agent.owner_id)


class Services:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.box = SecretBox(load_master_key(settings.master_key, settings.data_dir))
        self.db = Database(settings.db_url)
        self.events = EventBus()
        self.pool = SSHPool(settings.connect_timeout)
        self.audit = AuditLog(self.db)
        self.jobs = JobManager(self.db, self.pool, self.events, self.resolve_target, settings.job_poll_seconds)
        self.gateway = Gateway(self)
        self.approval_events: dict[str, asyncio.Event] = {}

    async def start(self) -> None:
        await self.db.create_all()
        await self._check_master_key()
        await self._bootstrap_admin()
        await self.jobs.resume()

    async def close(self) -> None:
        await self.jobs.shutdown()
        await self.pool.close_all()
        await self.db.close()

    async def _check_master_key(self) -> None:
        """Fail at startup, not at first use, if the key does not match the database."""
        async with self.db.session() as session:
            canary = await session.get(Setting, "key_canary")
            if canary is None:
                session.add(Setting(key="key_canary", value=self.box.encrypt("atb", "canary")))
                await session.commit()
            else:
                self.box.decrypt(canary.value, "canary")

    async def _bootstrap_admin(self) -> None:
        async with self.db.session() as session:
            if await session.scalar(select(func.count()).select_from(User)):
                return
            if not self.settings.admin_password:
                log.warning("no users exist yet; run `atb create-user NAME --admin`")
                return
            await self.create_user(self.settings.admin_username, self.settings.admin_password, "admin")

    async def create_user(self, username: str, password: str, role: str = "user") -> User:
        if len(password) < 10:
            raise PolicyDenied("passwords must be at least 10 characters")
        async with self.db.session() as session:
            if await session.scalar(select(User).where(User.username == username)):
                raise Conflict("that username is taken")
            user = User(username=username, password_hash=hash_password(password), role=role)
            session.add(user)
            await session.commit()
        return user

    # --- servers and grants -------------------------------------------------

    def target_for(self, server: Server) -> Target:
        return Target(
            server.id, server.host, server.port, server.username, server.auth_type,
            self.box.decrypt(server.secret_enc, f"server:{server.id}"), server.host_key,
        )

    async def resolve_target(self, server_id: str) -> tuple[Target, str]:
        async with self.db.session() as session:
            server = await session.get(Server, server_id)
        if server is None:
            raise NotFound("the server no longer exists")
        return self.target_for(server), server.owner_id

    async def bind_server(self, ctx: Context, ref: str) -> None:
        """Attach a server (by id or name) and the actor's grant on it to the context."""
        async with self.db.session() as session:
            query = select(Server).where((Server.id == ref) | (Server.name == ref))
            if ctx.actor.is_agent:
                query = query.join(Grant, Grant.server_id == Server.id).where(Grant.agent_id == ctx.actor.id)
            elif not ctx.actor.is_admin:
                query = query.where(Server.owner_id == ctx.actor.id)
            matches = list(await session.scalars(query))
            exact = [s for s in matches if s.id == ref]
            matches = exact or matches
            if not matches:
                raise NotFound(f"no server named {ref!r} is available to you")
            if len(matches) > 1:
                raise Conflict(f"several servers are named {ref!r}; use the id")
            server = matches[0]
            if ctx.actor.is_agent:
                grant = await session.scalar(
                    select(Grant).where(Grant.agent_id == ctx.actor.id, Grant.server_id == server.id)
                )
                ctx.grant = GrantConfig.model_validate(grant.config)
            else:
                ctx.grant = owner_grant()
        ctx.server = server
        ctx.target = self.target_for(server)

    # --- approvals ------------------------------------------------------------

    async def approval_wait(self, approval_id: str, seconds: float) -> Approval | None:
        async with self.db.session() as session:
            approval = await session.get(Approval, approval_id)
        if approval is None or approval.status != "pending" or seconds <= 0:
            return approval
        event = self.approval_events.setdefault(approval_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), seconds)
        except TimeoutError:
            pass
        async with self.db.session() as session:
            return await session.get(Approval, approval_id)

    async def decide_approval(self, approval_id: str, user: User, approve: bool, reason: str = "") -> Approval:
        async with self.db.session() as session:
            approval = await session.get(Approval, approval_id)
            server = await session.get(Server, approval.server_id) if approval else None
            if approval is None or server is None or (user.role != "admin" and server.owner_id != user.id):
                raise NotFound("no such approval request")
            changed = await session.execute(
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == "pending", Approval.expires_at > now())
                .values(
                    status="approved" if approve else "denied", decided_by=user.username, decided_at=now(),
                    reason=reason[:1000], expires_at=now() + self.settings.approval_ttl_seconds,
                )
            )
            if changed.rowcount != 1:
                raise Conflict("this request has already been decided or has expired")
            await session.commit()
            await session.refresh(approval)
        await self.audit.record(
            actor_type="user", actor_id=user.id, actor_name=user.username, owner_id=server.owner_id,
            server_id=server.id, action="approval_decision", status=approval.status,
            detail={"approval_id": approval_id, "summary": approval.summary, "reason": reason[:1000]},
        )
        if event := self.approval_events.pop(approval_id, None):
            event.set()
        self.events.publish("approval_decided", server.owner_id, approval_id=approval_id, status=approval.status)
        return approval

    # --- emergency stop -------------------------------------------------------

    async def kill_switch(self) -> bool:
        async with self.db.session() as session:
            setting = await session.get(Setting, "kill_switch")
        return setting is not None and setting.value == "1"

    async def set_kill_switch(self, enabled: bool) -> list[str]:
        async with self.db.session() as session:
            await session.merge(Setting(key="kill_switch", value="1" if enabled else "0"))
            await session.commit()
        return await self.stop_jobs() if enabled else []

    async def is_stopped(self, agent_id: str) -> bool:
        if await self.kill_switch():
            return True
        async with self.db.session() as session:
            agent = await session.get(Agent, agent_id)
        return agent is None or agent.paused

    async def stop_jobs(self, agent_id: str | None = None, server_id: str | None = None) -> list[str]:
        """Terminate running assistant commands; returns the ids that could not be stopped."""
        jobs = [j for j in await self.jobs.running(agent_id, server_id) if j.actor_type == "agent"]
        results = await asyncio.gather(*(self.jobs.terminate(job) for job in jobs), return_exceptions=True)
        failed = [job.id for job, result in zip(jobs, results, strict=False) if isinstance(result, BaseException)]
        for job_id in failed:
            log.error("emergency stop could not terminate job %s", job_id)
        # Pending approvals must not survive a stop.
        async with self.db.session() as session:
            query = update(Approval).where(Approval.status.in_(("pending", "approved")))
            if agent_id:
                query = query.where(Approval.agent_id == agent_id)
            if server_id:
                query = query.where(Approval.server_id == server_id)
            await session.execute(query.values(status="cancelled"))
            await session.commit()
        for event in self.approval_events.values():
            event.set()
        return failed
