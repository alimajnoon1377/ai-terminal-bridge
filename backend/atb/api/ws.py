"""WebSocket endpoints: live events, tool calls, and the human terminal.

Tokens are sent in the first message, never in the URL, so they stay out of proxy logs.
"""

import asyncio
import contextlib
import json
import re

import asyncssh
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import Server
from ..errors import BridgeError
from ..services import Services, user_actor
from .deps import actor_for_token, user_for_token

router = APIRouter(prefix="/api/ws")
_SESSION_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
_RECHECK_SECONDS = 15


async def hello(socket: WebSocket) -> dict:
    await socket.accept()
    try:
        message = await asyncio.wait_for(socket.receive_json(), 10)
        return message if isinstance(message, dict) else {}
    except (TimeoutError, ValueError, KeyError):
        return {}


async def run_until_first(*coroutines) -> None:
    tasks = [asyncio.ensure_future(c) for c in coroutines]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, RuntimeError)):
                raise exc
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def close_quietly(socket: WebSocket, code: int = 1000, reason: str = "") -> None:
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await socket.close(code, reason[:120])


@router.websocket("/events")
async def events(socket: WebSocket):
    services: Services = socket.app.state.services
    token = str((await hello(socket)).get("token", ""))
    user = await user_for_token(services, token)
    if user is None:
        return await close_quietly(socket, 4401, "unauthorized")

    with services.events.subscribe() as queue:

        async def forward():
            while True:
                event = await queue.get()
                if user.role == "admin" or event["owner_id"] == user.id or event["type"] == "kill_switch":
                    await socket.send_json(event)

        async def drain():
            while True:
                await socket.receive_text()

        async def still_signed_in():
            while await user_for_token(services, token) is not None:
                await asyncio.sleep(_RECHECK_SECONDS)

        await socket.send_json({"type": "ready"})
        await run_until_first(forward(), drain(), still_signed_in())
    await close_quietly(socket)


@router.websocket("/tools")
async def tools(socket: WebSocket):
    """Assistant channel: send {"id", "tool", "arguments"}, receive {"id", "ok", "result"|"error"}."""
    services: Services = socket.app.state.services
    token = str((await hello(socket)).get("token", ""))
    if await actor_for_token(services, token) is None:
        return await close_quietly(socket, 4401, "unauthorized")
    send_lock = asyncio.Lock()
    slots = asyncio.Semaphore(8)

    async def handle(message: dict):
        reply: dict = {"id": message.get("id")}
        try:
            actor = await actor_for_token(services, token)  # the token may have been revoked
            if actor is None:
                return await close_quietly(socket, 4401, "unauthorized")
            arguments = message.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise BridgeError("arguments must be an object")
            reply.update(ok=True, result=await services.gateway.call(actor, str(message.get("tool")), arguments))
        except BridgeError as exc:
            reply.update(ok=False, error=exc.to_dict())
        finally:
            slots.release()
        async with send_lock:
            await socket.send_json(reply)

    await socket.send_json({"type": "ready"})
    pending: set[asyncio.Task] = set()
    try:
        while True:
            try:
                message = json.loads(await socket.receive_text())
                assert isinstance(message, dict)
            except (ValueError, AssertionError):
                await socket.send_json({"id": None, "ok": False, "error": {"error": "invalid_input", "message": "expected a JSON object"}})
                continue
            await slots.acquire()
            task = asyncio.create_task(handle(message))
            pending.add(task)
            task.add_done_callback(pending.discard)
    except WebSocketDisconnect:
        pass
    finally:
        await asyncio.gather(*pending, return_exceptions=True)


@router.websocket("/terminal/{server_id}")
async def terminal(socket: WebSocket, server_id: str):
    """Interactive PTY for a signed-in human. With tmux on the target, sessions survive disconnects."""
    services: Services = socket.app.state.services
    opening = await hello(socket)
    token = str(opening.get("token", ""))
    user = await user_for_token(services, token)
    if user is None:
        return await close_quietly(socket, 4401, "unauthorized")
    async with services.db.session() as session:
        server = await session.get(Server, server_id)
    if server is None or (user.role != "admin" and server.owner_id != user.id):
        return await close_quietly(socket, 4404, "no such server")

    def dimension(value, default: int) -> int:
        return min(max(value, 2), 1000) if isinstance(value, int) else default

    name = str(opening.get("session") or "")
    if name and not _SESSION_NAME.match(name):
        return await close_quietly(socket, 4400, "invalid session name")
    actor = user_actor(user)
    audit = dict(actor_type="user", actor_id=user.id, actor_name=user.username, owner_id=server.owner_id, server_id=server.id)
    try:
        target = services.target_for(server)
        conn = await services.pool.get(target, actor.id)
        persistent = False
        if name:
            check = await services.pool.run(target, actor.id, ["sh", "-c", "command -v tmux"])
            persistent = check.exit_code == 0
        command = f"tmux new-session -A -s atb-{name}" if persistent else None
        process = await conn.create_process(
            command, term_type="xterm-256color", encoding=None, stderr=asyncssh.STDOUT,
            term_size=(dimension(opening.get("cols"), 80), dimension(opening.get("rows"), 24)),
        )
    except (BridgeError, OSError, asyncssh.Error) as exc:
        await socket.send_json({"type": "error", "message": str(exc)})
        return await close_quietly(socket, 1011, "connection failed")

    await services.audit.record(**audit, action="terminal_open", status="ok", detail={"persistent": persistent, "session": name})
    await socket.send_json({"type": "ready", "persistent": persistent})

    async def to_browser():
        while data := await process.stdout.read(32768):
            await socket.send_bytes(data)
        await socket.send_json({"type": "exit", "code": process.exit_status})

    async def from_browser():
        while True:
            message = json.loads(await socket.receive_text())
            if message.get("type") == "input":
                process.stdin.write(str(message.get("data", "")).encode())
            elif message.get("type") == "resize":
                process.change_terminal_size(dimension(message.get("cols"), 80), dimension(message.get("rows"), 24))

    async def still_signed_in():
        while await user_for_token(services, token) is not None:
            await asyncio.sleep(_RECHECK_SECONDS)

    try:
        await run_until_first(to_browser(), from_browser(), still_signed_in())
    except (asyncssh.Error, OSError, ValueError):
        pass
    finally:
        process.close()
        await services.audit.record(**audit, action="terminal_close", status="ok", detail={"session": name})
        await close_quietly(socket)
