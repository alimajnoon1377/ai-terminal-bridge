"""The shipped example agent, run as a user would run it, against a scripted model endpoint.

This checks the example's plumbing and the OpenAI-format interface, not any real model.
"""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "openai_agent.py"


def scripted_model(requests: list, calls: list) -> ThreadingHTTPServer:
    """Answer each request with the next tool call, then with a final message."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            step = len(requests) - 1
            if step < len(calls):
                name, arguments = calls[step]
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": f"call_{step}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)}}]}
            else:
                message = {"role": "assistant", "content": "all done"}
            data = json.dumps({"choices": [{"message": message}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def test_openai_agent_example(make_agent, bridge, workspace):
    agent = await make_agent("operator")
    requests: list = []
    model = scripted_model(requests, [
        ("list_servers", {}),
        ("write_file", {"server": "testbox", "path": "hello.py", "content": "print(2 + 3)\n"}),
        ("execute_command", {"server": "testbox", "command": "python3 hello.py", "wait_seconds": 20}),
    ])
    try:
        env = {**os.environ, "ATB_URL": bridge.url, "ATB_AGENT_TOKEN": agent.token,
               "OPENAI_BASE_URL": f"http://127.0.0.1:{model.server_port}/v1", "NO_PROXY": "127.0.0.1"}
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(EXAMPLE), "write and run a script", env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(process.communicate(), 60)
    finally:
        model.shutdown()
    assert process.returncode == 0, err.decode()
    assert out.decode().strip() == "all done"
    assert {t["function"]["name"] for t in requests[0]["tools"]} >= {"execute_command", "write_file"}
    assert (workspace.project / "hello.py").read_text() == "print(2 + 3)\n"
    executed = json.loads(requests[-1]["messages"][-1]["content"])
    assert executed["status"] == "succeeded" and executed["output"] == "5\n"
