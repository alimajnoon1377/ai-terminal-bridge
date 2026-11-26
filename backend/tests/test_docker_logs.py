"""Docker and log tools against a real Docker daemon.

Skipped unless ATB_TEST_DOCKER_IMAGE names a local image that provides `sh`, `sleep` and `echo`,
and the SSH test user may use Docker.
"""

import os
import subprocess
import uuid

import pytest

IMAGE = os.environ.get("ATB_TEST_DOCKER_IMAGE", "")
pytestmark = pytest.mark.skipif(not IMAGE, reason="set ATB_TEST_DOCKER_IMAGE to run the Docker tests")


@pytest.fixture
def container():
    name = f"atb-it-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "run", "-d", "--name", name, IMAGE, "sh", "-c", "echo container-says-hi; exec sleep 300"],
                   check=True, capture_output=True)
    yield name
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


async def test_docker_needs_explicit_grant(make_agent, container):
    agent = await make_agent("operator")
    await agent.tool("manage_docker", expect=403, server="testbox", action="ps")
    await agent.tool("get_logs", expect=403, server="testbox", source="docker", name=container)


async def test_container_lifecycle(make_agent, approve, container):
    agent = await make_agent("operator", allow_docker=True)
    assert container in (await agent.tool("manage_docker", server="testbox", action="ps"))["output"]
    assert (await agent.tool("manage_docker", server="testbox", action="images"))["output"]
    assert container in (await agent.tool("manage_docker", server="testbox", action="inspect", target=container))["output"]
    for logs in (await agent.tool("manage_docker", server="testbox", action="logs", target=container),
                 await agent.tool("get_logs", server="testbox", source="docker", name=container)):
        assert "container-says-hi" in logs.get("output", logs.get("log", ""))

    def state() -> str:
        return subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container], capture_output=True, text=True).stdout.strip()

    request = dict(server="testbox", action="stop", target=container)
    pending = await agent.tool("manage_docker", **request)
    assert pending["status"] == "approval_required" and state() == "true"
    await approve(pending["approval_id"])
    await agent.tool("manage_docker", **request)
    assert state() == "false"
    await agent.tool("manage_docker", server="testbox", action="start", target=container)
    assert state() == "true"


async def test_read_only_cannot_control_containers(make_agent, container):
    agent = await make_agent("read_only", allow_docker=True)
    await agent.tool("manage_docker", expect=403, server="testbox", action="restart", target=container)


async def test_option_injection_in_container_name(make_agent):
    agent = await make_agent("operator", allow_docker=True)
    for name in ("--all", "x; id", "$(id)"):
        await agent.tool("manage_docker", expect=422, server="testbox", action="inspect", target=name)


async def test_compose_up_shows_the_file_and_needs_approval(make_agent, approve, workspace):
    agent = await make_agent("operator", allow_docker=True)
    project = f"atbit{uuid.uuid4().hex[:6]}"
    workspace.write(f"{project}/compose.yaml",
                    f"services:\n  idle:\n    image: {IMAGE}\n    command: [sleep, '300']\n    network_mode: none\n")
    directory = str(workspace.project / project)
    request = dict(server="testbox", action="compose_up", project_path=directory)
    try:
        pending = await agent.tool("manage_docker", **request)
        assert pending["status"] == "approval_required"
        await approve(pending["approval_id"])
        await agent.tool("manage_docker", **request)
        assert "idle" in (await agent.tool("manage_docker", server="testbox", action="compose_ps", project_path=directory))["output"]
        down = dict(server="testbox", action="compose_down", project_path=directory)
        pending = await agent.tool("manage_docker", **down)
        await approve(pending["approval_id"])
        await agent.tool("manage_docker", **down)
    finally:
        subprocess.run(["docker", "compose", "--project-directory", directory, "down"], capture_output=True)


async def test_log_file_is_confined_and_redacted(make_agent, workspace):
    agent = await make_agent("read_only")
    workspace.write("app.log", "start\npassword=hunter2-very-secret\nend\n")
    result = await agent.tool("get_logs", server="testbox", source="file", name=str(workspace.project / "app.log"), lines=2)
    assert result["log"].endswith("end\n") and "start" not in result["log"]
    assert "hunter2" not in result["log"] and "[REDACTED" in result["log"]
    await agent.tool("get_logs", expect=403, server="testbox", source="file", name=str(workspace.outside / "secret.txt"))


async def test_listing_reports_symlinks(make_agent, workspace):
    (workspace.project / "alias").symlink_to("hello.txt")
    (workspace.project / "folder").mkdir()
    workspace.own()
    agent = await make_agent("read_only")
    entries = (await agent.tool("list_directory", server="testbox", path=str(workspace.project)))["entries"]
    kinds = {e["name"]: e["type"] for e in entries}
    assert kinds == {"hello.txt": "file", "alias": "symlink", "folder": "directory"}
