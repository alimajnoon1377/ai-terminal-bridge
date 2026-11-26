"""Authentication helpers shared by REST, WebSocket and MCP."""

from fastapi import Depends, Request
from sqlalchemy import select

from ..crypto import hash_token
from ..db import Agent, LoginSession, User, now
from ..errors import AuthFailed, PolicyDenied
from ..services import Services, agent_actor, user_actor
from ..tools.base import Actor


def get_services(request: Request) -> Services:
    return request.app.state.services


def bearer(header: str | None) -> str:
    scheme, _, token = (header or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


async def user_for_token(services: Services, token: str) -> User | None:
    if not token.startswith("atbs_"):
        return None
    async with services.db.session() as session:
        login = await session.get(LoginSession, hash_token(token))
        if login is None or login.expires_at < now():
            return None
        user = await session.get(User, login.user_id)
    return None if user is None or user.disabled else user


async def agent_for_token(services: Services, token: str) -> Agent | None:
    if not token.startswith("atbag_"):
        return None
    async with services.db.session() as session:
        agent = await session.scalar(select(Agent).where(Agent.token_hash == hash_token(token)))
        if agent is None:
            return None
        owner = await session.get(User, agent.owner_id)
    return None if owner is None or owner.disabled else agent


async def actor_for_token(services: Services, token: str) -> Actor | None:
    if agent := await agent_for_token(services, token):
        return agent_actor(agent)
    if user := await user_for_token(services, token):
        return user_actor(user)
    return None


async def current_user(request: Request, services: Services = Depends(get_services)) -> User:
    user = await user_for_token(services, bearer(request.headers.get("authorization")))
    if user is None:
        raise AuthFailed("sign in required")
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise PolicyDenied("administrator access required")
    return user


async def current_actor(request: Request, services: Services = Depends(get_services)) -> Actor:
    actor = await actor_for_token(services, bearer(request.headers.get("authorization")))
    if actor is None:
        raise AuthFailed("a valid assistant token or user session is required")
    return actor
