"""The single enforcement point. Every interface calls tools through `Gateway.call`."""

import asyncio
import hashlib
import json
import logging
from collections import deque

from pydantic import ValidationError
from sqlalchemy import func, select, update

from .db import Agent, Approval, new_id, now
from .errors import BridgeError, Conflict, NotFound, PolicyDenied, RateLimited, ValidationFailed
from .policy import Decision, Kind, decide
from .redact import redact, redact_value
from .tools import REGISTRY
from .tools.base import Actor, Context, Operation, ToolSpec

log = logging.getLogger(__name__)

_BULKY = {"content", "content_base64", "old_text", "new_text"}
_VOLATILE = {"wait_seconds"}  # arguments that do not change what an operation does
_MAX_PENDING = 25


def audit_arguments(arguments: dict) -> dict:
    """Arguments as recorded: file bodies become a digest, everything else is redacted."""
    cleaned = {}
    for key, value in arguments.items():
        if key in _BULKY and isinstance(value, str):
            cleaned[key] = {"chars": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()}
        else:
            text = redact_value(value)
            cleaned[key] = text[:4000] if isinstance(text, str) else text
    return cleaned


def known_secrets(ctx: Context) -> tuple[str, ...]:
    """Literal values to mask for this server, whatever pattern they do or do not match."""
    return (ctx.target.secret,) if ctx.target and ctx.target.auth_type == "password" else ()


class Gateway:
    def __init__(self, services):
        self.services = services
        self._calls: dict[str, deque] = {}
        self._approval_lock = asyncio.Lock()

    async def call(self, actor: Actor, name: str, arguments: dict | None = None) -> dict:
        arguments = arguments or {}
        ctx = Context(self.services, actor)
        summary, status = name, "ok"
        detail: dict = {"arguments": audit_arguments(arguments)}
        spec = REGISTRY.get(name)
        record = True
        try:
            if spec is None:
                raise NotFound(f"unknown tool {name!r}")
            if actor.is_agent:
                await self._admit(actor)
            args = self._parse(spec, arguments)
            operation = await self._prepare(ctx, spec, args)
            summary = operation.summary or name
            # Browsing by a signed-in human is not logged; everything that changes state is.
            record = actor.is_agent or not (spec.read_only or operation.kind == Kind.READ) or name == "terminate_command"
            self._publish(ctx, name, summary, "running")
            if ctx.grant is not None:
                verdict = decide(ctx.grant, operation.kind, destructive=operation.destructive, sandboxed=operation.sandboxed)
                if verdict.decision == Decision.APPROVE:
                    operation, pending = await self._approval(ctx, spec, args, operation, verdict.reason, detail)
                    if pending:
                        status = "approval_pending"
                        return pending
            result = await operation.run()
            for key in ("command_id", "change_id", "snapshot_id", "exit_code"):
                if result.get(key) is not None:
                    detail[key] = result[key]
            return self._clean(ctx, result)
        except BridgeError as exc:
            status = "denied" if isinstance(exc, (PolicyDenied, RateLimited)) else "error"
            detail["error"] = redact(exc.message)[:1000]
            if actor.is_agent:
                exc.message = redact(exc.message)
            raise
        except Exception as exc:
            log.exception("tool %s failed", name)
            status = "error"
            detail["error"] = f"internal error: {type(exc).__name__}"
            raise BridgeError(f"internal error ({type(exc).__name__}); see the bridge log") from exc
        finally:
            if record:
                server = ctx.server
                secrets = known_secrets(ctx)
                await self.services.audit.record(
                    actor_type=actor.type, actor_id=actor.id, actor_name=actor.name,
                    owner_id=server.owner_id if server else actor.owner_id,
                    server_id=server.id if server else "", action=name, status=status,
                    detail=redact_value({**detail, "summary": redact(summary, secrets)[:500]}, secrets),
                )
                self._publish(ctx, name, summary, status)

    def _publish(self, ctx: Context, tool: str, summary: str, status: str) -> None:
        owner = ctx.server.owner_id if ctx.server else ctx.actor.owner_id
        self.services.events.publish(
            "activity", owner, actor=ctx.actor.name, actor_type=ctx.actor.type, tool=tool,
            summary=redact(summary, known_secrets(ctx))[:300], status=status, server_id=ctx.server.id if ctx.server else "",
        )

    def _clean(self, ctx: Context, result: dict) -> dict:
        if not ctx.actor.is_agent:
            return result
        secrets = known_secrets(ctx)
        # Base64 bodies are screened before encoding; pattern matching would corrupt them.
        body = result.pop("content_base64", None)
        cleaned = redact_value(result, secrets)
        if body is not None:
            cleaned["content_base64"] = body
        return cleaned

    async def _admit(self, actor: Actor) -> None:
        if await self.services.kill_switch():
            raise PolicyDenied("the administrator has stopped all assistant activity")
        async with self.services.db.session() as session:
            agent = await session.get(Agent, actor.id)
            if agent is None or agent.paused:
                raise PolicyDenied("this assistant has been paused by its owner")
            agent.last_seen_at = now()
            limit = agent.rate_limit_per_minute
            await session.commit()
        calls = self._calls.setdefault(actor.id, deque())
        moment = now()
        while calls and calls[0] < moment - 60:
            calls.popleft()
        if len(calls) >= limit:
            raise RateLimited(f"rate limit of {limit} calls per minute reached")
        calls.append(moment)

    @staticmethod
    def _parse(spec: ToolSpec, arguments: dict):
        try:
            return spec.model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors())
            raise ValidationFailed(problems) from exc

    async def _prepare(self, ctx: Context, spec: ToolSpec, args) -> Operation:
        if spec.needs_server:
            await self.services.bind_server(ctx, args.server)
        try:
            return await spec.handler(ctx, args)
        except (OSError, TimeoutError) as exc:
            raise BridgeError(f"connection problem: {exc or type(exc).__name__}") from exc

    async def _approval(self, ctx: Context, spec: ToolSpec, args, operation: Operation, reason: str, detail: dict):
        """Return (operation, None) once approved, or (operation, response) while pending.

        An approval is bound to the exact arguments and remote state, and is used once.
        """
        fingerprint = self._fingerprint(ctx, spec, args, operation)
        approval = await self._find_or_create(ctx, spec, operation, reason, fingerprint)
        detail["approval_id"] = approval.id
        if approval.status == "pending":
            approval = await self.services.approval_wait(approval.id, self.services.settings.approval_wait_seconds)
        if approval.status == "denied":
            raise PolicyDenied(f"the request was denied by {approval.decided_by}: {approval.reason or 'no reason given'}")
        if approval.status == "pending":
            return operation, {
                "status": "approval_required",
                "approval_id": approval.id,
                "reason": reason,
                "summary": redact(operation.summary, known_secrets(ctx)),
                "next_step": "A human must approve this. Poll get_approval, then call the same tool with identical arguments.",
            }
        if approval.status != "approved":
            raise PolicyDenied(f"the approval request is {approval.status}")
        async with self.services.db.session() as session:
            used = await session.execute(
                update(Approval)
                .where(Approval.id == approval.id, Approval.status == "approved", Approval.expires_at > now())
                .values(status="executed")
            )
            await session.commit()
        if used.rowcount != 1:
            raise Conflict("that approval has already been used or has expired")
        # Time has passed: re-check the stop switch, the grant and the remote state.
        await self._admit(ctx.actor)
        fresh_ctx = Context(self.services, ctx.actor)
        fresh = await self._prepare(fresh_ctx, spec, args)
        decide(fresh_ctx.grant, fresh.kind, destructive=fresh.destructive, sandboxed=fresh.sandboxed)
        if self._fingerprint(fresh_ctx, spec, args, fresh) != fingerprint:
            raise Conflict("the file, permissions or environment changed after approval; request approval again")
        return fresh, None

    @staticmethod
    def _fingerprint(ctx: Context, spec: ToolSpec, args, operation: Operation) -> str:
        data = [
            ctx.actor.id, ctx.server.id, spec.name, args.model_dump(exclude=_VOLATILE, mode="json"),
            operation.state, operation.kind, operation.sandboxed, ctx.grant.model_dump(mode="json"),
        ]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    async def _find_or_create(self, ctx, spec, operation, reason, fingerprint) -> Approval:
        async with self._approval_lock, self.services.db.session() as session:
            await session.execute(
                update(Approval)
                .where(Approval.status.in_(("pending", "approved")), Approval.expires_at <= now())
                .values(status="expired")
            )
            existing = await session.scalar(
                select(Approval)
                .where(
                    Approval.agent_id == ctx.actor.id, Approval.fingerprint == fingerprint,
                    Approval.status.in_(("pending", "approved", "denied")), Approval.expires_at > now(),
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            if existing is not None:
                await session.commit()
                return existing
            pending = await session.scalar(
                select(func.count()).select_from(Approval)
                .where(Approval.agent_id == ctx.actor.id, Approval.status == "pending")
            )
            if pending >= _MAX_PENDING:
                raise RateLimited("too many approval requests are already waiting")
            approval_id = new_id()
            # The preview is never redacted, because masking could hide part of what is being
            # approved. It is encrypted at rest and shown only to the server's owner.
            preview = f"why approval is needed: {reason}\n\n{operation.preview or operation.summary}"
            approval = Approval(
                id=approval_id, agent_id=ctx.actor.id, server_id=ctx.server.id, tool=spec.name, fingerprint=fingerprint,
                summary=redact(operation.summary, known_secrets(ctx))[:1000],
                preview=self.services.box.encrypt(preview, f"approval:{approval_id}"),
                expires_at=now() + self.services.settings.approval_ttl_seconds,
            )
            session.add(approval)
            await session.commit()
        self.services.events.publish(
            "approval_requested", ctx.server.owner_id, approval_id=approval.id, actor=ctx.actor.name,
            summary=approval.summary, server_id=ctx.server.id,
        )
        return approval
