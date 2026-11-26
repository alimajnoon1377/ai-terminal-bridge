"""Git, test runners and Docker."""

import posixpath
import re
import shlex

from pydantic import Field

from ..errors import PolicyDenied, RemoteError, ValidationFailed
from ..policy import Kind, Level, decide
from ..ssh.files import sftp_session
from ..ssh.jobs import Sandbox, bwrap_argv
from .base import Context, Operation, ServerArgs, tool
from .execute import build_sandbox, plan_command, sandbox_available
from .system import valid_name

_GIT_READ = ("status", "diff", "log", "show", "branches")
_GIT_WRITE = ("init", "clone", "add", "commit", "checkout", "create_branch", "fetch", "pull", "push")
# Repository settings that make read-only git commands start external programs.
_UNSAFE_GIT_CONFIG = re.compile(
    r"^(filter\.|diff\..*\.(command|textconv)$|diff\.external$|core\.(fsmonitor|hookspath|sshcommand|pager|editor|worktree)$|include)",
    re.I,
)
_HARDENING = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]


class GitArgs(ServerArgs):
    operation: str = Field(pattern=f"^({'|'.join(_GIT_READ + _GIT_WRITE)})$")
    path: str = Field(".", description="Repository directory (for clone: the directory to create)")
    ref: str | None = Field(None, max_length=200, description="Branch, tag or commit")
    files: list[str] = Field(default_factory=list, max_length=500, description="Paths for add or diff")
    message: str | None = Field(None, max_length=5000)
    url: str | None = Field(None, max_length=2000, description="Repository URL for clone")
    staged: bool = False
    max_count: int = Field(30, ge=1, le=500)


def safe_ref(value: str) -> str:
    if value.startswith("-") or not re.match(r"^[A-Za-z0-9_./@{}~^\-]+$", value):
        raise ValidationFailed("invalid git ref")
    return value


def safe_url(value: str) -> str:
    # ext:: and similar transports execute arbitrary programs.
    if not re.match(r"^(https://|ssh://|git://|[A-Za-z0-9_.\-]+@[A-Za-z0-9_.\-]+:)[^\s]+$", value):
        raise ValidationFailed("only https, ssh and git repository URLs are allowed")
    return value


async def git_read(ctx: Context, repo: str, command: list[str]) -> dict:
    sandbox = None
    if ctx.actor.is_agent:
        if await sandbox_available(ctx):
            full = await build_sandbox(ctx, read_only=True, network=False)
            sandbox = Sandbox((), full.ro_paths, full.masked_dirs, False)
        else:
            listing = await ctx.run(["git", "-C", repo, "config", "--local", "--includes", "--name-only", "--list"])
            unsafe = [k for k in listing.stdout.split() if _UNSAFE_GIT_CONFIG.match(k)]
            if unsafe:
                raise PolicyDenied(
                    f"the repository config sets {', '.join(unsafe)}, which could run programs, and no sandbox is available"
                )
    argv = ["git", "-c", f"safe.directory={repo}", "-C", repo, *_HARDENING, "--no-pager", *command]
    if sandbox:
        argv = bwrap_argv(sandbox, repo) + argv
    result = await ctx.run(["bash", "-c", shlex.join(argv) + ' | head -c 200000; exit "${PIPESTATUS[0]}"'], timeout=60)
    if result.exit_code not in (0, 141):
        raise RemoteError(result.stderr.strip()[:1000] or f"git exited with {result.exit_code}")
    return {"output": result.stdout, "truncated": result.exit_code == 141}


async def repository_root(ctx: Context, path: str) -> str:
    """The enclosing repository must itself be inside the grant, not just the given directory."""
    probe = await ctx.run(
        ["git", "-c", "safe.directory=*", "-C", path, *_HARDENING, "rev-parse", "--show-toplevel", "--git-common-dir"]
    )
    lines = probe.stdout.splitlines()
    if probe.exit_code != 0 or len(lines) < 2:
        raise ValidationFailed(f"{path} is not inside a git working tree")
    root = await ctx.resolve(lines[0])
    await ctx.resolve(posixpath.join(path, lines[1]))
    return root


@tool("git_operations", GitArgs, "Inspect and change git repositories (status, diff, log, add, commit, branch, clone, pull, push ...).")
async def git_operations(ctx: Context, args: GitArgs) -> Operation:
    op = args.operation
    if op == "clone":
        if not args.url:
            raise ValidationFailed("url is required for clone")
        target = await ctx.resolve(args.path, write=True)
        command = shlex.join(["git", "clone", "--", safe_url(args.url), target])
        return await plan_command(ctx, command, cwd=posixpath.dirname(target), timeout=None, wait_seconds=40,
                                  summary=f"git clone into {target}")
    path = await ctx.resolve(args.path, write=op == "init")
    if op != "init":
        await repository_root(ctx, path)
    files = [await ctx.resolve(posixpath.join(path, f)) for f in args.files]
    if op in _GIT_READ:
        command = {
            "status": ["status", "--short", "--branch"],
            "diff": ["diff", "--no-ext-diff", "--no-textconv", *(["--cached"] if args.staged else [])],
            "log": ["log", f"--max-count={args.max_count}", "--date=iso", "--pretty=format:%h %ad %an %s"],
            "show": ["show", "--no-ext-diff", "--no-textconv", "--stat", "--patch"],
            "branches": ["branch", "--all", "--verbose"],
        }[op]
        if args.ref and op in ("diff", "log", "show"):
            command.append(safe_ref(args.ref))
        if op in ("diff", "log"):
            command += ["--", *files]

        async def run() -> dict:
            return await git_read(ctx, path, command)

        return Operation(run=run, summary=f"git {op} in {path}")

    argv = ["git"]
    if ctx.grant.level != Level.DEVELOPER and not ctx.grant.force_sandbox:
        argv += ["-c", "core.hooksPath=/dev/null"]  # never run repository hooks outside a sandbox
    else:
        identity = re.sub(r"[^\w .\-]", "", ctx.actor.name) or "assistant"
        argv += ["-c", f"user.name={identity}", "-c", "user.email=assistant@atb.invalid"]
    if op == "init":
        argv += ["init"]
    elif op == "add":
        if not files:
            raise ValidationFailed("files is required for add")
        argv += ["add", "--", *files]
    elif op == "commit":
        if not args.message:
            raise ValidationFailed("message is required for commit")
        argv += ["commit", "-m", args.message]
    elif op in ("checkout", "create_branch"):
        if not args.ref:
            raise ValidationFailed("ref is required")
        argv += ["checkout", *(["-b"] if op == "create_branch" else []), safe_ref(args.ref)]
    elif op == "pull":
        argv += ["pull", "--ff-only"]
    else:
        argv += [op]
        if op == "push" and args.ref:
            argv += ["origin", safe_ref(args.ref)]
    destructive = {"push": "publishes commits to a remote repository", "checkout": ""}.get(op, "")
    return await plan_command(ctx, shlex.join(argv), cwd=path, timeout=None, wait_seconds=40,
                              destructive=destructive, summary=f"git {op} in {path}")


class TestArgs(ServerArgs):
    path: str = Field(".", description="Project directory")
    framework: str = Field("auto", pattern="^(auto|pytest|npm|go|cargo|make)$")
    extra_args: list[str] = Field(default_factory=list, max_length=100)
    timeout_seconds: int | None = Field(None, ge=1, le=86400)
    wait_seconds: float = Field(40, ge=0, le=50)


_COMMANDS = {"pytest": ["python3", "-m", "pytest"], "npm": ["npm", "test"], "go": ["go", "test", "./..."],
             "cargo": ["cargo", "test"], "make": ["make", "test"]}
_MARKERS = [("pytest", ("pytest.ini", "pyproject.toml", "setup.py", "setup.cfg", "conftest.py", "tox.ini")),
            ("npm", ("package.json",)), ("go", ("go.mod",)), ("cargo", ("Cargo.toml",)), ("make", ("Makefile",))]


@tool("run_tests", TestArgs, "Run a project's test suite (pytest, npm, go, cargo or make).")
async def run_tests(ctx: Context, args: TestArgs) -> Operation:
    path = await ctx.resolve(args.path)
    framework = args.framework
    if framework == "auto":
        async with sftp_session(await ctx.connection()) as sftp:
            names = set(map(str, await sftp.listdir(path)))
        framework = next((fw for fw, files in _MARKERS if names.intersection(files)), "")
        if not framework and any(n.startswith("test_") and n.endswith(".py") for n in names):
            framework = "pytest"
        if not framework:
            raise ValidationFailed("no known test setup found; set the framework argument")
    argv = list(_COMMANDS[framework])
    if args.extra_args:
        argv += (["--"] if framework == "npm" else []) + args.extra_args
    return await plan_command(ctx, shlex.join(argv), cwd=path, timeout=args.timeout_seconds,
                              wait_seconds=args.wait_seconds, summary=f"run {framework} tests in {path}")


_COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")


class DockerArgs(ServerArgs):
    action: str = Field(pattern="^(ps|images|inspect|logs|start|stop|restart|remove|compose_ps|compose_up|compose_down)$")
    target: str | None = Field(None, description="Container name or id")
    project_path: str | None = Field(None, description="Directory containing the compose file")
    lines: int = Field(200, ge=1, le=2000)


@tool("manage_docker", DockerArgs, "Inspect and control Docker containers and Compose projects.")
async def manage_docker(ctx: Context, args: DockerArgs) -> Operation:
    action = args.action
    if ctx.grant.level != Level.OWNER and not ctx.grant.allow_docker:
        raise PolicyDenied("Docker access is not enabled for this grant")
    read = action in ("ps", "images", "inspect", "logs", "compose_ps")
    if not read:
        decide(ctx.grant, Kind.DOCKER)  # refuse before touching the server
    directory = compose_file = ""
    if action.startswith("compose_"):
        if not args.project_path:
            raise ValidationFailed("project_path is required")
        directory = await ctx.resolve(args.project_path, write=action != "compose_ps")
        # Naming the file stops Compose from searching parent directories outside the grant.
        async with sftp_session(await ctx.connection()) as sftp:
            for name in _COMPOSE_FILES:
                candidate = posixpath.join(directory, name)
                if await sftp.exists(candidate):
                    compose_file = await ctx.resolve(candidate, sftp=sftp)
                    break
        if not compose_file:
            raise ValidationFailed(f"no compose file in {directory}")
        compose = ["docker", "compose", "--project-directory", directory, "-f", compose_file]
        sub = {"compose_ps": ["ps", "--all"], "compose_up": ["up", "-d"], "compose_down": ["down"]}[action]
        argv = [*compose, *sub]
    elif action == "ps":
        argv = ["docker", "ps", "--all", "--format", "{{json .}}"]
    elif action == "images":
        argv = ["docker", "images", "--format", "{{json .}}"]
    else:
        if not args.target:
            raise ValidationFailed("target is required")
        name = valid_name(args.target, "container")
        argv = {
            "inspect": ["docker", "inspect", "--", name],
            "logs": ["docker", "logs", "--tail", str(args.lines), "--", name],
            "remove": ["docker", "rm", "--", name],
        }.get(action, ["docker", action, "--", name])
    destructive = {
        "stop": "stops a container", "remove": "removes a container", "compose_down": "stops and removes containers",
        "compose_up": "starts containers; a compose file can grant root-equivalent access to the host",
    }.get(action, "")
    preview = shlex.join(argv)
    state = ""
    if action == "compose_up":
        # Show the approver the compose file that will actually run, and bind the approval to it.
        shown = await ctx.run([*compose, "config"], timeout=60)
        if shown.exit_code != 0:
            raise RemoteError(shown.stderr.strip()[:1000])
        preview += "\n\n" + shown.stdout[:30000]
        state = shown.stdout

    async def run() -> dict:
        command = f"{shlex.join(argv)} 2>&1 | tail -c 200000"
        result = await ctx.run(["bash", "-c", command + '; exit "${PIPESTATUS[0]}"'], timeout=175)
        if result.exit_code != 0:
            raise RemoteError(result.stdout.strip()[-1000:] or f"docker exited with {result.exit_code}")
        return {"action": action, "output": result.stdout}

    return Operation(run=run, kind=Kind.READ if read else Kind.DOCKER, summary=f"docker {action} {args.target or directory}".strip(),
                     preview=preview, destructive=destructive, state=state)


