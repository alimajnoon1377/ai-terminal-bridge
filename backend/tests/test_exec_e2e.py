"""Command execution: sandbox, background jobs, termination, recovery, git and tests."""

import asyncio
import os
import subprocess

import pytest


async def finish(agent, result: dict) -> dict:
    output = result["output"]
    while result["status"] == "running" or result["more_output"]:
        result = await agent.tool("get_command_output", command_id=result["command_id"], offset=result["next_offset"])
        output += result["output"]
    return {**result, "output": output}


def is_alive(pid: int) -> bool:
    """True unless the process is gone or a zombie waiting to be reaped."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rpartition(")")[2].split()[0] != "Z"
    except FileNotFoundError:
        return False


def sandbox_works(user: str) -> bool:
    command = ["bwrap", "--unshare-pid", "--unshare-net", "--ro-bind", "/", "/", "--proc", "/proc", "true"]
    if os.geteuid() == 0:
        command = ["su", user, "-c", " ".join(command)]
    try:
        return subprocess.run(command, capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.fixture
def needs_sandbox(ssh_user):
    if not sandbox_works(ssh_user):
        pytest.skip("bubblewrap is not usable here")


async def test_connect_and_info(make_agent):
    agent = await make_agent("operator")
    info = await agent.tool("connect_server", server="testbox")
    assert info["connected"] and info["system"] == "Linux" and not info["is_root"]
    system = await agent.tool("get_system_info", server="testbox")
    assert system["cpu_count"] >= 1 and system["memory"]["total_bytes"] > 0 and system["disks"]
    assert 0 <= system["cpu_percent"] <= 100
    processes = await agent.tool("get_processes", server="testbox", filter="sshd", limit=5)
    assert processes["processes"]
    assert (await agent.tool("disconnect_server", server="testbox"))["disconnected"]


async def test_exit_codes_and_cwd(make_agent, workspace):
    agent = await make_agent("operator")
    ok = await finish(agent, await agent.tool("execute_command", server="testbox", command="pwd; echo err >&2"))
    assert ok["status"] == "succeeded" and ok["output"] == f"{workspace.project}\nerr\n" and not ok["sandboxed"]
    bad = await finish(agent, await agent.tool("execute_command", server="testbox", command="printf partial; exit 3"))
    assert (bad["status"], bad["exit_code"], bad["output"]) == ("failed", 3, "partial")
    await agent.tool("execute_command", expect=403, server="testbox", command="ls", cwd="/etc")


async def test_background_streaming_and_terminate(make_agent, workspace):
    agent = await make_agent("operator")
    started = await agent.tool(
        "execute_command", server="testbox", wait_seconds=1.5,
        command="echo first; sleep 60 & echo $! > child.pid; wait",
    )
    assert started["status"] == "running" and "first" in started["output"]
    child = int((workspace.project / "child.pid").read_text())
    stopped = await agent.tool("terminate_command", command_id=started["command_id"])
    assert stopped["status"] == "terminated"
    await asyncio.sleep(0.3)
    assert not is_alive(child)


async def test_terminate_kills_processes_that_ignore_term(make_agent):
    agent = await make_agent("operator")
    started = await agent.tool("execute_command", server="testbox", wait_seconds=1, command="trap '' TERM; sleep 60")
    assert (await agent.tool("terminate_command", command_id=started["command_id"]))["status"] == "terminated"


async def test_timeout_and_limits(make_agent):
    agent = await make_agent("operator", max_runtime_seconds=2, max_output_kb=1, max_concurrent_jobs=1)
    slow = await finish(agent, await agent.tool("execute_command", server="testbox", command="sleep 30", timeout_seconds=500))
    assert slow["status"] == "timed_out" and slow["exit_code"] == 124
    noisy = await finish(agent, await agent.tool("execute_command", server="testbox", command="yes x | head -c 100000"))
    assert len(noisy["output"]) == 1024 and noisy["output_limit_reached"]
    running = await agent.tool("execute_command", server="testbox", command="sleep 30", wait_seconds=0)
    await agent.tool("execute_command", expect=409, server="testbox", command="true")
    await agent.tool("terminate_command", command_id=running["command_id"])


async def test_jobs_are_private(make_agent):
    first, second = await make_agent("operator"), await make_agent("operator")
    job = await first.tool("execute_command", server="testbox", command="echo mine")
    await second.tool("get_command_output", expect=404, command_id=job["command_id"])
    await second.tool("terminate_command", expect=404, command_id=job["command_id"])


async def test_destructive_command_needs_approval(make_agent, workspace, approve):
    agent = await make_agent("operator")
    call = dict(server="testbox", command="rm hello.txt")
    pending = await agent.tool("execute_command", **call)
    assert pending["status"] == "approval_required" and (workspace.project / "hello.txt").exists()
    await approve(pending["approval_id"])
    assert (await finish(agent, await agent.tool("execute_command", **call)))["status"] == "succeeded"
    assert not (workspace.project / "hello.txt").exists()


async def test_allow_and_deny_lists(make_agent, workspace):
    agent = await make_agent("operator", allowed_commands=["echo"])
    out = await agent.tool("execute_command", server="testbox", command="echo hi; touch pwned")
    assert "touch pwned" in out["output"] and not (workspace.project / "pwned").exists()
    await agent.tool("execute_command", expect=403, server="testbox", command="touch pwned")
    strict = await make_agent("operator", denied_patterns=[r"\bcurl\b"])
    await strict.tool("execute_command", expect=403, server="testbox", command="curl http://example.com")


async def test_developer_sandbox_confines_commands(make_agent, workspace, needs_sandbox):
    agent = await make_agent("developer")

    async def run(command):
        return await finish(agent, await agent.tool("execute_command", server="testbox", command=command))

    inside = await run("cat hello.txt && echo made > created.txt")
    assert inside["status"] == "succeeded" and inside["sandboxed"]
    assert (workspace.project / "created.txt").read_text() == "made\n"
    escape = await run(f"cat {workspace.outside}/secret.txt; ls /home; echo x > {workspace.outside}/pwned")
    assert "outside secret" not in escape["output"] and not (workspace.outside / "pwned").exists()
    (workspace.project / "link").symlink_to(workspace.outside / "secret.txt")
    assert "outside secret" not in (await run("cat link"))["output"]
    system = await run("echo x > /usr/pwned; echo $HOME; ps -e --no-headers | wc -l")
    assert "Read-only file system" in system["output"] and "/tmp/home" in system["output"]
    network = await run("python3 -c \"import socket; socket.create_connection(('127.0.0.1', 22), 1)\"")
    assert network["status"] == "failed"
    workspace.write(".ssh/id_ed25519", "PRIVATE-MARKER")
    assert "PRIVATE-MARKER" not in (await run("cat .ssh/id_ed25519"))["output"]


async def test_sandboxed_terminate(make_agent, needs_sandbox):
    agent = await make_agent("developer")
    job = await agent.tool("execute_command", server="testbox", command="echo up; sleep 60", wait_seconds=1.5)
    assert job["status"] == "running"
    assert (await agent.tool("terminate_command", command_id=job["command_id"]))["status"] == "terminated"


async def test_developer_cannot_mount_the_home_directory(make_agent, needs_sandbox):
    agent = await make_agent("developer", paths=[{"path": "/home", "mode": "rw"}])
    denied = await agent.tool("execute_command", expect=403, server="testbox", command="true", cwd="/home")
    assert "home directory" in denied["message"]


async def test_developer_without_sandbox_needs_approval(make_agent, monkeypatch):
    from atb.tools import execute

    async def unavailable(_ctx):
        return False

    monkeypatch.setattr(execute, "sandbox_available", unavailable)
    agent = await make_agent("developer")
    result = await agent.tool("execute_command", server="testbox", command="id")
    assert result["status"] == "approval_required"
    forced = await make_agent("operator", force_sandbox=True)
    await forced.tool("execute_command", expect=403, server="testbox", command="id")


async def test_run_tests(make_agent, workspace, needs_sandbox):
    agent = await make_agent("developer")
    workspace.write("Makefile", "test:\n\t@python3 -m unittest -q test_math\n")
    workspace.write("test_math.py", "import unittest\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(1 + 1, 3)\n")
    failing = await finish(agent, await agent.tool("run_tests", server="testbox", framework="make"))
    assert failing["status"] == "failed" and "FAILED" in failing["output"]
    await agent.tool("edit_file", server="testbox", path="test_math.py", old_text=", 3)", new_text=", 2)  # fixed")
    passing = await finish(agent, await agent.tool("run_tests", server="testbox"))
    assert passing["status"] == "succeeded", passing["output"]


async def test_git_operations(make_agent, workspace, needs_sandbox):
    agent = await make_agent("developer")

    async def git(**kwargs):
        result = await agent.tool("git_operations", server="testbox", **kwargs)
        return await finish(agent, result) if "command_id" in result else result

    assert (await git(operation="init"))["status"] == "succeeded"
    assert "hello.txt" in (await git(operation="status"))["output"]
    assert (await git(operation="add", files=["hello.txt"]))["status"] == "succeeded"
    committed = await git(operation="commit", message="first; $(touch injected)")
    assert committed["status"] == "succeeded", committed["output"]
    assert not (workspace.project / "injected").exists()
    assert "first; $(touch injected)" in (await git(operation="log"))["output"]
    await agent.tool("git_operations", expect=422, server="testbox", operation="checkout", ref="--orphan")
    await agent.tool("git_operations", expect=422, server="testbox", operation="clone", path="c", url="ext::sh -c id")


async def test_git_config_cannot_escape_the_grant(make_agent, workspace, monkeypatch):
    """A planted fsmonitor program must not run unsandboxed through a read-only git call."""
    workspace.write("hook.sh", f"#!/bin/sh\ntouch {workspace.outside}/pwned\n").chmod(0o755)
    subprocess.run(["git", "init", "-q", str(workspace.project)], check=True)
    with (workspace.project / ".git/config").open("a") as config:
        config.write(f'[filter "x"]\n\tclean = {workspace.project}/hook.sh\n')
    workspace.write(".gitattributes", "*.txt filter=x\n")
    from atb.tools import devops

    async def unavailable(_ctx):
        return False

    monkeypatch.setattr(devops, "sandbox_available", unavailable)
    agent = await make_agent("read_only")
    denied = await agent.tool("git_operations", expect=403, server="testbox", operation="status")
    assert "filter.x.clean" in denied["message"]
    assert not (workspace.outside / "pwned").exists()


async def test_repository_above_the_grant_is_refused(make_agent, workspace):
    subprocess.run(["git", "init", "-q", str(workspace.base)], check=True)
    workspace.own()
    agent = await make_agent("operator")
    await agent.tool("git_operations", expect=403, server="testbox", operation="log")


async def test_kill_switch_stops_everything(make_agent, admin, approve):
    agent = await make_agent("operator")
    job = await agent.tool("execute_command", server="testbox", command="sleep 60", wait_seconds=0.5)
    pending = await agent.tool("execute_command", server="testbox", command="rm hello.txt")
    try:
        state = await admin.request("PUT", "/api/settings/kill-switch", json={"enabled": True})
        assert state["not_stopped"] == []
        jobs = await admin.request("GET", "/api/jobs")
        assert next(j for j in jobs if j["id"] == job["command_id"])["status"] == "terminated"
        await agent.tool("list_servers", expect=403)
        await admin.request("POST", f"/api/approvals/{pending['approval_id']}/decision", expect=409, json={"approve": True})
    finally:
        await admin.request("PUT", "/api/settings/kill-switch", json={"enabled": False})
    await agent.tool("list_servers")


async def test_changing_a_grant_stops_running_commands(make_agent, admin, server, workspace):
    agent = await make_agent("operator")
    job = await agent.tool("execute_command", server="testbox", command="sleep 60", wait_seconds=0.5)
    await admin.request("PUT", f"/api/agents/{agent.id}/grants/{server['id']}", json={"level": "read_only"})
    jobs = await admin.request("GET", "/api/jobs")
    assert next(j for j in jobs if j["id"] == job["command_id"])["status"] == "terminated"


async def test_job_survives_bridge_restart(make_agent, bridge):
    agent = await make_agent("operator")
    job = await agent.tool("execute_command", server="testbox", command="echo before; sleep 3; echo after", wait_seconds=1)
    assert job["status"] == "running"
    await bridge.stop()
    await asyncio.sleep(3)
    await bridge.start()
    done = await finish(agent, await agent.tool("get_command_output", command_id=job["command_id"]))
    assert done["status"] == "succeeded" and done["output"] == "before\nafter\n"


async def test_reconnects_after_ssh_server_restart(make_agent, sshd):
    agent = await make_agent("operator")
    await agent.tool("execute_command", server="testbox", command="true")
    # Drop every established session, as a network failure would.
    subprocess.run(["pkill", "-f", f"sshd.*{sshd.user}"], check=False)
    await asyncio.sleep(0.5)
    result = await agent.tool("execute_command", server="testbox", command="echo back")
    assert result["output"] == "back\n"


async def test_changed_host_key_is_refused(make_agent, sshd):
    agent = await make_agent("operator")
    await agent.tool("disconnect_server", server="testbox")
    sshd.stop()
    sshd.start("rogue_key")
    try:
        await agent.tool("connect_server", expect=502, server="testbox")
        failed = await agent.tool("execute_command", expect=502, server="testbox", command="id")
        assert "HOST KEY MISMATCH" in failed["message"]
    finally:
        sshd.stop()
        sshd.start()
    assert (await agent.tool("connect_server", server="testbox"))["connected"]
