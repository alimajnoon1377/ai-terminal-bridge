"""Access levels, path confinement, approvals, rollback, redaction and audit over real SSH."""

import asyncio
import base64

import httpx
from conftest import database_dump
from sqlalchemy import update

from atb.db import AuditEntry


async def test_secrets_are_never_returned_and_are_encrypted(admin, server, bridge):
    listed = await admin.request("GET", "/api/servers")
    assert all("secret" not in key for record in listed for key in record)
    raw = await database_dump(bridge)
    assert b"BEGIN OPENSSH PRIVATE KEY" not in raw


async def test_a_stored_password_is_masked_even_when_typed_into_a_call(admin, bridge, sshd):
    """The literal value matches no secret pattern, so it must be masked because the bridge knows it."""
    password = "plain-looking-value-7f3a9c"
    record = await admin.request(
        "POST", "/api/servers", expect=201,
        json={"name": "pwbox", "host": "127.0.0.1", "port": sshd.port, "username": "nobody",
              "auth_type": "password", "secret": password})
    agent = await admin.request("POST", "/api/agents", expect=201, json={"name": "pw-agent"})
    try:
        await admin.request("PUT", f"/api/agents/{agent['id']}/grants/{record['id']}", json={"level": "operator"})
        async with httpx.AsyncClient(base_url=bridge.url, headers={"Authorization": f"Bearer {agent['token']}"}) as http:
            # Fails because the host key is not trusted yet; the attempt is still audited.
            response = await http.post("/api/v1/tools/execute_command", json={"server": "pwbox", "command": f"echo {password}"})
        assert response.status_code >= 400 and password not in response.text
        assert password.encode() not in await database_dump(bridge)
        entries = await admin.request("GET", f"/api/audit?actor_id={agent['id']}")
        assert "stored-credential" in str(entries)
        assert (await admin.request("GET", "/api/audit/verify"))["valid"]
    finally:
        await admin.request("DELETE", f"/api/agents/{agent['id']}", expect=204)
        await admin.request("DELETE", f"/api/servers/{record['id']}", expect=204)


async def test_read_only(make_agent, workspace):
    agent = await make_agent("read_only")
    path = str(workspace.project / "hello.txt")
    assert (await agent.tool("read_file", server="testbox", path=path))["content"].startswith("hello world")
    relative = await agent.tool("read_file", server="testbox", path="hello.txt", start_line=2, max_lines=1)
    assert relative["content"] == "second line\n"
    await agent.tool("write_file", expect=403, server="testbox", path=path, content="x")
    await agent.tool("execute_command", expect=403, server="testbox", command="id")
    await agent.tool("delete_file", expect=403, server="testbox", path=path)
    await agent.tool("create_directory", expect=403, server="testbox", path="newdir")
    assert (workspace.project / "hello.txt").read_text() == "hello world\nsecond line\n"
    assert not (workspace.project / "newdir").exists()


async def test_path_confinement(make_agent, workspace):
    agent = await make_agent("autonomous")
    (workspace.project / "link").symlink_to(workspace.outside)
    (workspace.project / "filelink").symlink_to(workspace.outside / "secret.txt")
    (workspace.project / "dangling").symlink_to(workspace.outside / "not-yet")
    workspace.write(".ssh/id_ed25519", "private")
    for path in (
        str(workspace.outside / "secret.txt"),
        str(workspace.project / ".." / "outside" / "secret.txt"),
        "link/secret.txt",
        "filelink",
        "/etc/passwd",
        ".ssh/id_ed25519",
    ):
        await agent.tool("read_file", expect=403, server="testbox", path=path)
    await agent.tool("write_file", expect=403, server="testbox", path="link/new.txt", content="x")
    await agent.tool("write_file", expect=422, server="testbox", path="dangling", content="x")
    assert not (workspace.outside / "not-yet").exists() and not (workspace.outside / "new.txt").exists()
    names = {e["name"] for e in (await agent.tool("list_directory", server="testbox", path="."))["entries"]}
    assert "hello.txt" in names and ".ssh" not in names
    # Deleting a symlink removes the link, never its target.
    await agent.tool("delete_file", server="testbox", path="filelink")
    assert (workspace.outside / "secret.txt").exists() and not (workspace.project / "filelink").is_symlink()
    await agent.tool("delete_file", expect=403, server="testbox", path=str(workspace.project), recursive=True)


async def test_nested_read_only_path(make_agent, workspace):
    workspace.write("vendor/lib.txt", "vendored")
    agent = await make_agent("autonomous", auto_approve_destructive=True, paths=[
        {"path": str(workspace.project), "mode": "rw"}, {"path": str(workspace.project / "vendor"), "mode": "ro"}])
    await agent.tool("read_file", server="testbox", path="vendor/lib.txt")
    await agent.tool("write_file", expect=403, server="testbox", path="vendor/lib.txt", content="x")
    workspace.write("tree/vendor.txt", "x")
    await agent.tool("delete_file", server="testbox", path="tree", recursive=True)


async def test_write_edit_search_and_rollback(make_agent, workspace):
    agent = await make_agent("operator")
    created = await agent.tool("write_file", server="testbox", path="src/app.py", content="x = 1\ny = 1\n",
                               create_directories=True)
    assert created["created"] and (workspace.project / "src/app.py").read_text() == "x = 1\ny = 1\n"
    await agent.tool("edit_file", expect=422, server="testbox", path="src/app.py", old_text="= 1", new_text="= 2")
    edited = await agent.tool("edit_file", server="testbox", path="src/app.py", old_text="x = 1", new_text="x = 2")
    found = await agent.tool("search_files", server="testbox", pattern="x = [0-9]", path=".")
    assert found["matches"] == [{"path": str(workspace.project / "src/app.py"), "line": 1, "text": "x = 2"}]
    by_name = await agent.tool("search_files", server="testbox", pattern="*.py", mode="name")
    assert [m["path"] for m in by_name["matches"]] == [str(workspace.project / "src/app.py")]
    await agent.tool("rollback_changes", server="testbox", change_id=edited["change_id"])
    assert (workspace.project / "src/app.py").read_text() == "x = 1\ny = 1\n"
    await agent.tool("rollback_changes", expect=409, server="testbox", change_id=edited["change_id"])
    deleted = await agent.tool("delete_file", server="testbox", path="src/app.py")
    assert deleted["recoverable"]
    await agent.tool("rollback_changes", server="testbox", change_id=deleted["change_id"])
    assert (workspace.project / "src/app.py").exists()
    # Rollback refuses to overwrite newer work.
    again = await agent.tool("write_file", server="testbox", path="src/app.py", content="z\n")
    workspace.write("src/app.py", "edited by a human\n")
    await agent.tool("rollback_changes", expect=409, server="testbox", change_id=again["change_id"])
    other = await make_agent("operator")
    await other.tool("rollback_changes", expect=404, server="testbox", change_id=again["change_id"])


async def test_upload_download(make_agent, workspace):
    agent = await make_agent("operator")
    blob = bytes(range(256)) * 100
    encoded = base64.b64encode(blob).decode()
    await agent.tool("upload_file", server="testbox", path="data.bin", content_base64=encoded)
    assert (workspace.project / "data.bin").read_bytes() == blob
    await agent.tool("upload_file", expect=409, server="testbox", path="data.bin", content_base64=encoded)
    assert base64.b64decode((await agent.tool("download_file", server="testbox", path="data.bin"))["content_base64"]) == blob
    await agent.tool("read_file", expect=422, server="testbox", path="data.bin")


async def test_snapshot_restore_needs_approval_and_keeps_old_tree(make_agent, workspace, approve):
    agent = await make_agent("operator")
    workspace.write("site/index.html", "v1")
    snapshot = await agent.tool("create_snapshot", server="testbox", path="site", label="before")
    workspace.write("site/index.html", "v2")
    workspace.write("site/extra.txt", "new")
    pending = await agent.tool("rollback_changes", server="testbox", snapshot_id=snapshot["snapshot_id"])
    assert pending["status"] == "approval_required"
    assert (workspace.project / "site/index.html").read_text() == "v2"
    await approve(pending["approval_id"])
    done = await agent.tool("rollback_changes", server="testbox", snapshot_id=snapshot["snapshot_id"])
    assert (workspace.project / "site/index.html").read_text() == "v1"
    assert not (workspace.project / "site/extra.txt").exists()
    assert (workspace.project / done["previous_content_kept_at"] / "extra.txt").read_text() == "new"


async def test_approval_flow(make_agent, workspace, approve, admin):
    agent = await make_agent("approval_required")
    call = dict(server="testbox", path="hello.txt", content="approved content\n")
    pending = await agent.tool("write_file", **call)
    assert pending["status"] == "approval_required"
    assert (await agent.tool("write_file", **call))["approval_id"] == pending["approval_id"]
    queue = await admin.request("GET", "/api/approvals", params={"status": "pending"})
    preview = next(a for a in queue if a["id"] == pending["approval_id"])["preview"]
    assert "-hello world" in preview and "+approved content" in preview
    # An assistant cannot approve its own request.
    await agent.request("POST", f"/api/approvals/{pending['approval_id']}/decision", expect=401, json={"approve": True})
    await approve(pending["approval_id"])
    status = await agent.tool("get_approval", approval_id=pending["approval_id"])
    assert status["status"] == "approved"
    # The approval covers only the exact request.
    different = await agent.tool("write_file", server="testbox", path="hello.txt", content="something else\n")
    assert different["status"] == "approval_required" and different["approval_id"] != pending["approval_id"]
    assert (await agent.tool("write_file", **call))["bytes"] == len(call["content"])
    assert (workspace.project / "hello.txt").read_text() == "approved content\n"
    # Single use: once consumed, the same request needs a new approval.
    workspace.write("hello.txt", "hello world\nsecond line\n")
    repeat = await agent.tool("write_file", **call)
    assert repeat["status"] == "approval_required" and repeat["approval_id"] != pending["approval_id"]
    await approve(repeat["approval_id"], ok=False, reason="not now")
    refused = await agent.tool("write_file", expect=403, **call)
    assert "not now" in refused["message"]
    assert (workspace.project / "hello.txt").read_text() == "hello world\nsecond line\n"


async def test_approval_is_void_when_the_file_changes(make_agent, workspace, approve):
    agent = await make_agent("approval_required")
    call = dict(server="testbox", path="hello.txt", content="mine\n")
    pending = await agent.tool("write_file", **call)
    await approve(pending["approval_id"])
    workspace.write("hello.txt", "changed by someone else\n")
    again = await agent.tool("write_file", **call)
    assert again["status"] == "approval_required" and again["approval_id"] != pending["approval_id"]
    assert (workspace.project / "hello.txt").read_text() == "changed by someone else\n"


async def test_waiting_call_resumes_when_approved(make_agent, workspace, approve, admin, bridge):
    agent = await make_agent("approval_required")
    bridge.settings.approval_wait_seconds = 15
    try:
        task = asyncio.create_task(agent.tool("create_directory", server="testbox", path="made-after-approval"))
        for _ in range(100):
            queue = await admin.request("GET", "/api/approvals", params={"status": "pending"})
            mine = [a for a in queue if a["agent_id"] == agent.id]
            if mine:
                break
            await asyncio.sleep(0.05)
        assert not (workspace.project / "made-after-approval").exists()
        await approve(mine[0]["id"])
        assert (await task)["created"]
    finally:
        bridge.settings.approval_wait_seconds = 0.2


async def test_redaction_and_placeholder_protection(make_agent, workspace):
    agent = await make_agent("operator")
    secret = "DB_PASSWORD=s3cr3t-value-123\nDEBUG=1\n"
    workspace.write(".env", secret)
    shown = await agent.tool("read_file", server="testbox", path=".env")
    assert "s3cr3t-value-123" not in shown["content"] and "[REDACTED:" in shown["content"]
    await agent.tool("write_file", expect=422, server="testbox", path=".env", content=shown["content"])
    await agent.tool("download_file", expect=403, server="testbox", path=".env")
    output = await agent.tool("execute_command", server="testbox", command="cat .env")
    assert "s3cr3t-value-123" not in output["output"] and "DEBUG=1" in output["output"]
    await agent.tool("edit_file", server="testbox", path=".env", old_text="DEBUG=1", new_text="DEBUG=0")
    assert (workspace.project / ".env").read_text() == "DB_PASSWORD=s3cr3t-value-123\nDEBUG=0\n"


async def test_no_grant_no_access(admin, bridge, server):
    from conftest import Client

    created = await admin.request("POST", "/api/agents", expect=201, json={"name": "nobody"})
    outsider = Client(bridge, created["token"])
    assert (await outsider.tool("list_servers"))["servers"] == []
    await outsider.tool("connect_server", expect=404, server="testbox")
    await outsider.tool("connect_server", expect=404, server=server["id"])
    await outsider.request("GET", "/api/servers", expect=401)
    await admin.request("DELETE", f"/api/agents/{created['id']}", expect=204)
    await outsider.tool("list_servers", expect=401)


async def test_users_are_isolated(admin, bridge, server, make_agent, approve):
    from conftest import login

    await admin.request("POST", "/api/users", expect=201,
                        json={"username": "bob", "password": "bobs-long-password"})
    bob = await login(bridge, "bob", "bobs-long-password")
    agent = await make_agent("approval_required")
    pending = await agent.tool("create_directory", server="testbox", path="x")
    assert await bob.request("GET", "/api/servers") == []
    assert await bob.request("GET", "/api/agents") == []
    assert await bob.request("GET", "/api/approvals") == []
    assert all(entry["actor_name"] == "bob" for entry in await bob.request("GET", "/api/audit"))
    await bob.tool("connect_server", expect=404, server=server["id"])
    await bob.request("DELETE", f"/api/servers/{server['id']}", expect=404)
    await bob.request("PUT", f"/api/agents/{agent.id}/grants/{server['id']}", expect=404, json={"level": "autonomous"})
    await bob.request("POST", f"/api/approvals/{pending['approval_id']}/decision", expect=404, json={"approve": True})
    await bob.request("GET", "/api/users", expect=403)
    await bob.request("PUT", "/api/settings/kill-switch", expect=403, json={"enabled": True})


async def test_rate_limit_and_pause(make_agent, admin):
    agent = await make_agent("operator")
    await admin.request("PATCH", f"/api/agents/{agent.id}", json={"rate_limit_per_minute": 2})
    await agent.tool("list_servers")
    await agent.tool("list_servers")
    await agent.tool("list_servers", expect=429)
    await admin.request("PATCH", f"/api/agents/{agent.id}", json={"rate_limit_per_minute": 100, "paused": True})
    await agent.tool("list_servers", expect=403)


async def test_login_lockout(bridge):
    import httpx

    async with httpx.AsyncClient(base_url=bridge.url) as http:
        for _ in range(8):
            assert (await http.post("/api/auth/login", json={"username": "mallory", "password": "x"})).status_code == 401
        assert (await http.post("/api/auth/login", json={"username": "mallory", "password": "x"})).status_code == 429


async def test_invalid_input_does_not_echo_secrets(admin):
    response = await admin.http.post("/api/servers", json={"name": "bad name!", "host": "h", "username": "u",
                                                           "secret": "TOPSECRET", "bogus": 1})
    assert response.status_code == 422 and "TOPSECRET" not in response.text


async def test_audit_chain_detects_tampering(admin, bridge, make_agent):
    agent = await make_agent("read_only")
    await agent.tool("execute_command", expect=403, server="testbox", command="id")
    entries = await admin.request("GET", "/api/audit", params={"actor_id": agent.id})
    assert entries[0]["action"] == "execute_command" and entries[0]["status"] == "denied"
    assert (await admin.request("GET", "/api/audit/verify"))["valid"]
    seq, original = entries[0]["seq"], entries[0]["status"]
    async with bridge.services.db.session() as session:
        await session.execute(update(AuditEntry).where(AuditEntry.seq == seq).values(status="ok"))
        await session.commit()
    broken = await admin.request("GET", "/api/audit/verify")
    assert broken == {"valid": False, "broken_at": seq, "entries": seq - 1}
    async with bridge.services.db.session() as session:
        await session.execute(update(AuditEntry).where(AuditEntry.seq == seq).values(status=original))
        await session.commit()
    assert (await admin.request("GET", "/api/audit/verify"))["valid"]


async def test_symlinked_grant_root_cannot_widen_access(make_agent, workspace):
    agent = await make_agent("operator", paths=[
        {"path": str(workspace.project), "mode": "rw"}, {"path": str(workspace.project / "later"), "mode": "ro"}])
    await agent.tool("read_file", server="testbox", path="hello.txt")
    (workspace.project / "later").symlink_to("/")
    denied = await agent.tool("read_file", expect=403, server="testbox", path="/etc/passwd")
    assert "symbolic link" in denied["message"]


async def test_approver_sees_the_exact_command(make_agent, admin, bridge):
    agent = await make_agent("approval_required")
    command = 'API_TOKEN="$(touch hidden-payload)" true'
    pending = await agent.tool("execute_command", server="testbox", command=command)
    queue = await admin.request("GET", "/api/approvals", params={"status": "pending"})
    assert command in next(a for a in queue if a["id"] == pending["approval_id"])["preview"]
    raw = await database_dump(bridge)
    assert b"touch hidden-payload" not in raw
