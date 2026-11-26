"""FastAPI application factory."""

import contextlib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from . import __version__
from .api import admin, toolapi, ws
from .api.deps import actor_for_token, agent_for_token, bearer
from .config import Settings, get_settings
from .errors import BridgeError
from .mcp_server import build_server
from .services import Services
from .tools import load_plugins

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self' data:; worker-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'; object-src 'none'"
)
_MAX_BODY = 8 * 1024 * 1024


class McpGate:
    """Require an assistant token before a request reaches the MCP transport."""

    def __init__(self, services: Services, inner):
        self._services, self._inner = services, inner

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers") or [])
        token = bearer(headers.get(b"authorization", b"").decode("latin-1"))
        if await agent_for_token(self._services, token) is None:
            response = JSONResponse(
                {"error": "unauthorized", "message": "an assistant token is required"},
                status_code=401, headers={"WWW-Authenticate": "Bearer"},
            )
            return await response(scope, receive, send)
        await self._inner(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    load_plugins()
    services = Services(settings)

    async def mcp_list(_ctx):
        return toolapi.tool_catalog()

    async def mcp_call(ctx, name, arguments):
        actor = await actor_for_token(services, bearer(ctx.request.headers.get("authorization")))
        if actor is None or not actor.is_agent:
            return {"error": "unauthorized", "message": "an assistant token is required"}, True
        try:
            return await services.gateway.call(actor, name, arguments), False
        except BridgeError as exc:
            return exc.to_dict(), True

    mcp_server = build_server(mcp_list, mcp_call)
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp", json_response=True, stateless_http=True, max_request_body_size=_MAX_BODY,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=Settings.split_list(settings.allowed_hosts),
            allowed_origins=Settings.split_list(settings.allowed_origins),
        ),
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        await services.start()
        try:
            async with mcp_server.session_manager.run():
                yield
        finally:
            await services.close()

    app = FastAPI(title="AI Terminal Bridge", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.services = services
    app.router.routes.append(Route("/mcp", endpoint=McpGate(services, mcp_app)))
    app.include_router(admin.router)
    app.include_router(toolapi.router)
    app.include_router(ws.router)

    @app.exception_handler(BridgeError)
    async def bridge_error(_request: Request, exc: BridgeError):
        return JSONResponse(exc.to_dict(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        # Do not echo submitted values back: they may contain passwords or keys.
        problems = "; ".join(f"{'.'.join(map(str, e['loc'][1:])) or 'body'}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"error": "invalid_input", "message": problems}, status_code=422)

    @app.middleware("http")
    async def harden(request: Request, call_next):
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > _MAX_BODY:
            return JSONResponse({"error": "invalid_input", "message": "request body too large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/docs"):
            return response  # the interactive docs page loads its assets from a CDN
        response.headers["Content-Security-Policy"] = _CSP
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__}

    static = Path(settings.static_dir).resolve() if settings.static_dir else None
    if static and (static / "index.html").is_file():

        @app.get("/{path:path}", include_in_schema=False)
        async def dashboard(path: str):
            if path == "api" or path.startswith("api/"):
                return JSONResponse({"error": "not_found", "message": "unknown endpoint"}, status_code=404)
            candidate = (static / path).resolve()
            if path and candidate.is_relative_to(static) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(static / "index.html")

    return app
