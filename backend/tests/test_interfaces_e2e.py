"""MCP (HTTP and stdio), OpenAI-compatible tool calls, WebSocket channels and the CLI."""

import asyncio
import json
import os
import subprocess
import sys

import httpx2
import websockets
from mcp.client.client import Client as MCPClient
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client


async def exercise_mcp(client: MCPClient, workspace):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert len(tools) == 25 and "execute_command" in tools
    assert tools["read_file"].input_schema["required"] == ["server", "path"]
    ran = await client.call_tool("execute_command", {"server": "testbox", "command": "cat hello.txt"})
    assert not ran.is_error and ran.structured_content["output"].startswith("hello world")
    assert json.loads(ran.content[0].text) == ran.structured_content
    denied = await client.call_tool("read_file", {"server": "testbox", "path": "/etc/passwd"})
    assert denied.is_error and denied.structured_content["error"] == "policy_denied"
    invalid = await client.call_tool("read_file", {"server": "testbox"})
    assert invalid.is_error and invalid.structured_content["error"] == "invalid_input"


async def test_mcp_over_http(bridge, make_agent, workspace, admin):
    agent = await make_agent("operator")
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {agent.token}"}, timeout=60) as http:
        async with MCPClient(streamable_http_client(f"{bridge.url}/mcp", http_client=http)) as client:
            await exercise_mcp(client, workspace)
    audit = await admin.request("GET", "/api/audit", params={"actor_id": agent.id})
    assert {"execute_command", "read_file"} <= {entry["action"] for entry in audit}


async def test_mcp_requires_an_assistant_token(bridge, admin):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    async with httpx2.AsyncClient(base_url=bridge.url) as http:
        for headers in ({}, {"Authorization": "Bearer atbag_wrong"}, {"Authorization": f"Bearer {admin.token}"}):
            assert (await http.post("/mcp", json=body, headers=headers)).status_code == 401


async def test_mcp_rejects_foreign_host_header(bridge, make_agent):
    agent = await make_agent("read_only")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Authorization": f"Bearer {agent.token}", "Host": "evil.example",
               "Accept": "application/json, text/event-stream"}
    async with httpx2.AsyncClient(base_url=bridge.url) as http:
        assert (await http.post("/mcp", json=body, headers=headers)).status_code == 421


async def test_mcp_over_stdio_proxy(bridge, make_agent, workspace):
    agent = await make_agent("operator")
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "atb", "mcp-stdio"],
        env={**os.environ, "ATB_URL": bridge.url, "ATB_AGENT_TOKEN": agent.token},
    )
    async with MCPClient(stdio_client(parameters)) as client:
        await exercise_mcp(client, workspace)


async def test_openai_tool_calling(make_agent, workspace):
    agent = await make_agent("operator")
    definitions = (await agent.request("GET", "/api/v1/openai/tools"))["tools"]
    assert all(d["type"] == "function" and d["function"]["parameters"]["type"] == "object" for d in definitions)
    reply = await agent.request("POST", "/api/v1/openai/tool_calls", json={"tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file",
         "arguments": json.dumps({"server": "testbox", "path": "hello.txt"})}},
        {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "not json"}},
        {"id": "c3", "type": "function", "function": {"name": "nope", "arguments": "{}"}},
    ]})
    first, second, third = reply["messages"]
    assert first["tool_call_id"] == "c1" and "hello world" in json.loads(first["content"])["content"]
    assert json.loads(second["content"])["error"] == "invalid_input"
    assert json.loads(third["content"])["error"] == "not_found"


async def receive_until(socket, predicate, timeout=15):
    async with asyncio.timeout(timeout):
        while True:
            raw = await socket.recv()
            message = raw if isinstance(raw, bytes) else json.loads(raw)
            if predicate(message):
                return message


async def test_websocket_tools_and_events(bridge, admin, make_agent):
    agent = await make_agent("operator")
    async with websockets.connect(f"{bridge.ws_url}/api/ws/events") as events:
        await events.send(json.dumps({"token": admin.token}))
        await receive_until(events, lambda m: m["type"] == "ready")
        async with websockets.connect(f"{bridge.ws_url}/api/ws/tools") as channel:
            await channel.send(json.dumps({"token": agent.token}))
            await receive_until(channel, lambda m: m.get("type") == "ready")
            await channel.send(json.dumps({"id": 7, "tool": "execute_command",
                                           "arguments": {"server": "testbox", "command": "echo live; sleep 1; echo more"}}))
            seen = await receive_until(events, lambda m: m["type"] == "job_output" and "live" in m["data"])
            reply = await receive_until(channel, lambda m: m.get("id") == 7)
            assert reply["ok"] and reply["result"]["output"] == "live\nmore\n" and seen["job_id"] == reply["result"]["command_id"]
            await receive_until(events, lambda m: m["type"] == "job_finished" and m["status"] == "succeeded")


async def test_websocket_rejects_bad_tokens(bridge, make_agent, server):
    agent = await make_agent("operator")
    for path, token in (("events", agent.token), (f"terminal/{server['id']}", agent.token), ("tools", "nonsense")):
        async with websockets.connect(f"{bridge.ws_url}/api/ws/{path}") as socket:
            await socket.send(json.dumps({"token": token}))
            try:
                await socket.recv()
                raise AssertionError(f"{path} accepted an invalid token")
            except websockets.ConnectionClosed as closed:
                assert closed.rcvd.code == 4401


async def open_terminal(bridge, admin, server, session=""):
    socket = await websockets.connect(f"{bridge.ws_url}/api/ws/terminal/{server['id']}")
    await socket.send(json.dumps({"token": admin.token, "cols": 100, "rows": 30, "session": session}))
    ready = await receive_until(socket, lambda m: isinstance(m, dict))
    assert ready["type"] == "ready", ready
    return socket, ready


async def read_terminal(socket, needle: bytes):
    buffer = b""

    def found(message):
        nonlocal buffer
        if isinstance(message, bytes):
            buffer += message
        return needle in buffer

    await receive_until(socket, found)
    return buffer


async def test_terminal_session_survives_disconnect(bridge, admin, server, sshd):
    socket, ready = await open_terminal(bridge, admin, server, "e2e")
    if not ready["persistent"]:
        await socket.close()
        import pytest

        pytest.skip("tmux is not installed")
    await socket.send(json.dumps({"type": "input", "data": "MARK=kept-$((40+2)); echo ready-$MARK\n"}))
    await read_terminal(socket, b"ready-kept-42")
    await socket.close()
    again, _ = await open_terminal(bridge, admin, server, "e2e")
    await again.send(json.dumps({"type": "input", "data": "echo still-$MARK\n"}))
    await read_terminal(again, b"still-kept-42")
    await again.send(json.dumps({"type": "input", "data": "exit\n"}))
    await again.close()


async def test_cli_call(bridge, make_agent):
    agent = await make_agent("read_only")
    env = {**os.environ, "ATB_URL": bridge.url, "ATB_AGENT_TOKEN": agent.token}
    done = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-m", "atb", "call", "list_servers"], env=env, capture_output=True, text=True
    )
    assert done.returncode == 0 and json.loads(done.stdout)["servers"][0]["name"] == "testbox"
