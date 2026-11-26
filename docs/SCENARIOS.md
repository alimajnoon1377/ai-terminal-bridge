# Practical scenarios

Each scenario has a matching automated test that runs against a real SSH server and bridge (no mocks). The examples use `atb call`; assistants call the same tools through MCP.

```bash
export ATB_URL=http://127.0.0.1:8750 ATB_AGENT_TOKEN=TOKEN
```

(With the Docker installation: `docker compose exec -e ATB_AGENT_TOKEN=TOKEN bridge atb call ...`)

## 1. Fixing a bug: write, test, correct, commit and roll back

Grant: `developer` on `/srv/projects/demo`.

```bash
atb call write_file '{"server":"web1","path":"calc.py","content":"def add(a, b):\n    return a - b\n"}'
atb call run_tests '{"server":"web1","framework":"make"}'          # fails
atb call search_files '{"server":"web1","pattern":"return a"}'
atb call edit_file '{"server":"web1","path":"calc.py","old_text":"a - b","new_text":"a + b"}'
atb call run_tests '{"server":"web1","framework":"make"}'          # passes
atb call git_operations '{"server":"web1","operation":"init"}'
atb call git_operations '{"server":"web1","operation":"add","files":["calc.py"]}'
atb call git_operations '{"server":"web1","operation":"commit","message":"fix add"}'
atb call rollback_changes '{"server":"web1","change_id":"CHANGE_ID"}'
```

This example assumes the project already has a `Makefile` and a test file.
Test: `test_scenario_fix_a_bug_and_commit`

## 2. Installing dependencies: the network is closed by default

In `developer`, commands have no network; they do after `sandbox_network` is turned on. The test checks network access only (to a local service); installing a package from the internet has not been tested.

```bash
atb call execute_command '{"server":"web1","command":"python3 -m venv .venv && .venv/bin/pip install -r requirements.txt","timeout_seconds":600}'
```

Test: `test_scenario_network_is_off_until_granted`

## 3. A destructive operation with human approval

Grant: `operator`.

```bash
atb call execute_command '{"server":"web1","command":"rm old.log"}'
# {"status":"approval_required","approval_id":"..."}  <- a human approves it in the dashboard
atb call get_approval '{"approval_id":"ID","wait_seconds":50}'
atb call execute_command '{"server":"web1","command":"rm old.log"}'   # the same call
```

Test: `test_scenario_a_human_approves_a_destructive_command`

## 4. Long-running work and disconnects

```bash
atb call execute_command '{"server":"web1","command":"make build","wait_seconds":2,"timeout_seconds":600}'
atb call disconnect_server '{"server":"web1"}'
atb call get_command_output '{"command_id":"ID","offset":0,"wait_seconds":50}'
```

Tests: `test_scenario_long_command_survives_a_disconnect`, `test_job_survives_bridge_restart`

## 5. Several servers at once

An assistant gets a separate grant for each server and sends calls in parallel (over WebSocket or concurrent requests). A server it has no access to is "not found" for the assistant.

Test: `test_scenario_two_servers_at_once` (two independent sshd instances on one machine)

## 6. Emergency stop

An administrator presses the stop button: running commands are terminated, pending approvals are cancelled and later calls are rejected with `policy_denied`.

Tests: `test_scenario_emergency_stop`, `test_kill_switch_stops_everything`

## 7. Restoring a whole folder

```bash
atb call create_snapshot '{"server":"web1","path":"/srv/projects/demo","label":"before refactor"}'
atb call rollback_changes '{"server":"web1","snapshot_id":"ID"}'   # requires approval
```

The replaced content is not deleted; it stays next to the folder with the suffix `.atb-before-...`.
Test: `test_snapshot_restore_needs_approval_and_keeps_old_tree`

## Running the tests

Requires Linux, `sshd`, `bubblewrap`, `git`, `make`, `tmux` and a regular user:

```bash
pip install -e "./backend[dev]"
cd backend
ATB_TEST_USER=someuser python -m pytest      # when the tests are run as root
```

The Docker tests run only with `ATB_TEST_DOCKER_IMAGE=<image>`, and the test user must be a member of the docker group.
