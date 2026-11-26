"""The practical scenarios described in docs/SCENARIOS.md, run end to end."""

import asyncio
import subprocess

from conftest import SSHServer
from test_exec_e2e import finish, needs_sandbox  # noqa: F401  (needs_sandbox is a fixture)


async def run(agent, command: str, **extra) -> dict:
    return await finish(agent, await agent.tool("execute_command", server="testbox", command=command, **extra))


async def test_scenario_fix_a_bug_and_commit(make_agent, workspace, needs_sandbox):  # noqa: F811
    """Write code, run the tests, read the failure, fix it, commit, then undo the fix."""
    agent = await make_agent("developer")
    await agent.tool("write_file", server="testbox", path="calc.py", content="def add(a, b):\n    return a - b\n")
    await agent.tool(
        "write_file", server="testbox", path="test_calc.py",
        content="import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(2, 2), 4)\n",
    )
    await agent.tool("write_file", server="testbox", path="Makefile", content="test:\n\tpython3 -B -m unittest -q test_calc\n")
    failing = await finish(agent, await agent.tool("run_tests", server="testbox", framework="make"))
    assert failing["status"] == "failed" and "AssertionError" in failing["output"] and failing["sandboxed"]

    found = await agent.tool("search_files", server="testbox", pattern="return a")
    assert found["matches"][0]["path"].endswith("calc.py")
    fix = await agent.tool("edit_file", server="testbox", path="calc.py", old_text="a - b", new_text="a + b")
    passing = await finish(agent, await agent.tool("run_tests", server="testbox", framework="make"))
    assert passing["status"] == "succeeded", passing["output"]

    for arguments in ({"operation": "init"}, {"operation": "add", "files": ["calc.py", "test_calc.py", "Makefile"]},
                      {"operation": "commit", "message": "fix add"}):
        done = await finish(agent, await agent.tool("git_operations", server="testbox", **arguments))
        assert done["status"] == "succeeded", done["output"]
    assert "fix add" in (await agent.tool("git_operations", server="testbox", operation="log"))["output"]

    await agent.tool("rollback_changes", server="testbox", change_id=fix["change_id"])
    assert "a - b" in (workspace.project / "calc.py").read_text()


async def test_scenario_network_is_off_until_granted(make_agent, bridge, needs_sandbox):  # noqa: F811
    """Dependency installation needs the sandbox network, which is off by default."""
    probe = f"python3 -c \"import urllib.request; print(urllib.request.urlopen('{bridge.url}/api/health', timeout=3).status)\""
    offline = await run(await make_agent("developer"), probe)
    assert offline["sandboxed"] and offline["status"] == "failed"
    online = await run(await make_agent("developer", sandbox_network=True), probe)
    assert online["sandboxed"] and online["output"].strip() == "200", online["output"]


async def test_scenario_a_human_approves_a_destructive_command(make_agent, admin, approve, workspace):
    agent = await make_agent("operator")
    pending = await agent.tool("execute_command", server="testbox", command="rm hello.txt")
    assert pending["status"] == "approval_required" and (workspace.project / "hello.txt").exists()
    listed = await admin.request("GET", "/api/approvals?status=pending")
    assert "rm hello.txt" in next(a for a in listed if a["id"] == pending["approval_id"])["preview"]
    await approve(pending["approval_id"])
    assert (await agent.tool("get_approval", approval_id=pending["approval_id"]))["status"] == "approved"
    done = await finish(agent, await agent.tool("execute_command", server="testbox", command="rm hello.txt"))
    assert done["status"] == "succeeded" and not (workspace.project / "hello.txt").exists()


async def test_scenario_long_command_survives_a_disconnect(make_agent):
    agent = await make_agent("operator")
    job = await agent.tool(
        "execute_command", server="testbox", command="echo start; sleep 2; echo end", wait_seconds=0.5)
    assert job["status"] == "running"
    left = await agent.tool("disconnect_server", server="testbox")
    assert job["command_id"] in left["commands_still_running"]
    await asyncio.sleep(2)
    done = await finish(agent, await agent.tool("get_command_output", command_id=job["command_id"]))
    assert done["status"] == "succeeded" and done["output"] == "start\nend\n"


async def test_scenario_two_servers_at_once(make_agent, admin, ssh_user, shared_root):
    """One assistant works on two machines concurrently (two independent SSH daemons)."""
    root = shared_root / "sshd-second"
    root.mkdir()
    second = SSHServer(root, ssh_user)
    second.start()
    record = None
    try:
        record = await admin.request(
            "POST", "/api/servers", expect=201,
            json={"name": "secondbox", "host": "127.0.0.1", "port": second.port, "username": ssh_user})
        with second.authorized.open("a") as handle:
            handle.write(record["public_key"] + "\n")
        probe = await admin.request("POST", f"/api/servers/{record['id']}/probe")
        await admin.request("POST", f"/api/servers/{record['id']}/trust", json={"fingerprint": probe["fingerprint"]})
        agent = await make_agent("operator")
        await agent.tool("list_servers")
        await agent.tool("execute_command", expect=404, server="secondbox", command="true")  # not granted yet
        await admin.request("PUT", f"/api/agents/{agent.id}/grants/{record['id']}", json={"level": "operator"})
        loop = asyncio.get_running_loop()
        started = loop.time()
        results = await asyncio.gather(*(
            agent.tool("execute_command", server=name, command="sleep 2; echo $SSH_CONNECTION", wait_seconds=15)
            for name in ("testbox", "secondbox")))
        assert loop.time() - started < 3.8, "the two commands did not overlap"
        ports = [r["output"].split()[-1] for r in results]
        assert ports[1] == str(second.port) and ports[0] != ports[1]
    finally:
        if record:
            await admin.request("DELETE", f"/api/servers/{record['id']}", expect=204)
        second.stop()
        subprocess.run(["rm", "-rf", str(root)], check=False)


async def test_scenario_emergency_stop(make_agent, admin):
    agent = await make_agent("operator")
    job = await agent.tool("execute_command", server="testbox", command="sleep 60", wait_seconds=0.5)
    try:
        await admin.request("PUT", "/api/settings/kill-switch", json={"enabled": True})
        refused = await agent.tool("list_servers", expect=403)
        assert refused["error"] == "policy_denied"
    finally:
        await admin.request("PUT", "/api/settings/kill-switch", json={"enabled": False})
    after = await agent.tool("get_command_output", command_id=job["command_id"])
    assert after["status"] == "terminated"
