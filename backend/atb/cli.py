"""Command line interface: `atb serve`, user management, MCP stdio proxy and a tool client."""

import asyncio
import json
import logging
import sys

import typer

from .config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="AI Terminal Bridge")


@app.command()
def serve(host: str = "", port: int = 0):
    """Run the bridge (REST, WebSocket, MCP and dashboard)."""
    import uvicorn

    from .app import create_app

    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("asyncssh").setLevel(logging.WARNING)
    uvicorn.run(create_app(settings), host=host or settings.host, port=port or settings.port, ws_max_size=1_048_576)


def _with_services(action):
    from .services import Services

    async def main():
        services = Services(get_settings())
        await services.db.create_all()
        try:
            return await action(services)
        finally:
            await services.db.close()

    return asyncio.run(main())


@app.command("create-user")
def create_user(
    username: str,
    admin: bool = typer.Option(False, help="Grant administrator rights"),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True, envvar="ATB_NEW_PASSWORD"),
):
    """Create a dashboard user."""
    from .errors import BridgeError

    try:
        _with_services(lambda s: s.create_user(username, password, "admin" if admin else "user"))
    except BridgeError as exc:
        typer.echo(f"error: {exc.message}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"created user {username}")


@app.command("verify-audit")
def verify_audit():
    """Check the hash chain of the audit log."""
    result = _with_services(lambda s: s.audit.verify())
    typer.echo(json.dumps(result))
    if not result["valid"]:
        raise typer.Exit(1)


@app.command("generate-key")
def generate_master_key():
    """Print a new value for ATB_MASTER_KEY."""
    from .crypto import generate_key

    typer.echo(generate_key())


URL = typer.Option("http://127.0.0.1:8750", envvar="ATB_URL", help="Address of a running bridge")
TOKEN = typer.Option(..., envvar="ATB_AGENT_TOKEN", help="Assistant token (prefer the environment variable)")


@app.command("mcp-stdio")
def mcp_stdio(url: str = URL, token: str = TOKEN):
    """MCP over stdio, forwarding to a running bridge (for desktop MCP clients)."""
    from .mcp_server import run_stdio_proxy

    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    asyncio.run(run_stdio_proxy(url, token))


@app.command("tools")
def list_tools():
    """List the tools the bridge provides."""
    from .tools import REGISTRY

    for spec in REGISTRY.values():
        typer.echo(f"{spec.name:22} {spec.description}")


@app.command("call")
def call(tool: str, arguments: str = typer.Argument("{}", help="JSON object, or - to read it from stdin"),
         url: str = URL, token: str = TOKEN):
    """Call one tool on a running bridge."""
    import httpx

    try:
        payload = json.loads(sys.stdin.read() if arguments == "-" else arguments)
        response = httpx.post(f"{url.rstrip('/')}/api/v1/tools/{tool}", json=payload,
                              headers={"Authorization": f"Bearer {token}"}, timeout=200)
        typer.echo(json.dumps(response.json(), indent=2, ensure_ascii=False))
    except (ValueError, httpx.HTTPError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if response.is_error:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
