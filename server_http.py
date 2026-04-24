"""
server_http.py — HTTP transport entry point for the MediaWiki MCP server.

Runs the same MCP app as server.py but over Streamable HTTP (MCP spec 2025-03-26)
using Starlette + uvicorn. Intended for hosted/Docker deployments where multiple
users connect remotely instead of running the server locally.

Client config (in .mcp.json or equivalent):
  {
    "mcpServers": {
      "mediawiki": {
        "type": "http",
        "url": "https://your-host/mcp",
        "headers": { "Authorization": "Bearer <MCP_API_KEY>" }
      }
    }
  }

Auth:
  All requests must include an Authorization: Bearer <MCP_API_KEY> header.
  Set MCP_API_KEY in .env. If unset, auth is disabled (not recommended in production).
"""

import logging
import uvicorn
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

# Import the MCP app object and config from the existing server module.
# This means all tool definitions live in one place (server.py).
from server import app as mcp_app
from config import get_config

config = get_config()


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

class APIKeyMiddleware(BaseHTTPMiddleware):
    """Rejects requests that don't carry a valid Bearer token.

    MCP_API_KEY accepts a single key or a comma-separated list of keys,
    allowing different keys to be issued to different users/teams.
    """

    def __init__(self, app, api_keys: str | None):
        super().__init__(app)
        self.api_keys = (
            {k.strip() for k in api_keys.split(",") if k.strip()}
            if api_keys else set()
        )

    async def dispatch(self, request: Request, call_next):
        if not self.api_keys:
            return await call_next(request)

        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in self.api_keys:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        return await call_next(request)


# ---------------------------------------------------------------------------
# MCP session manager + Starlette app
# ---------------------------------------------------------------------------

session_manager = StreamableHTTPSessionManager(
    app=mcp_app,
    stateless=True,   # No server-side session state — simpler and easier to scale
)


@asynccontextmanager
async def lifespan(app):
    async with session_manager.run():
        yield


async def handle_mcp(scope, receive, send):
    await session_manager.handle_request(scope, receive, send)


mcp_handler = APIKeyMiddleware(handle_mcp, api_keys=config["MCP_API_KEY"])


async def health(request: Request):
    return JSONResponse({"status": "ok"})


starlette_app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/health", health),
        Mount("/mcp", app=mcp_handler),
    ],
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = config["PORT"]
    print(f"Starting MediaWiki MCP HTTP server on port {port}")
    if not config["MCP_API_KEY"]:
        print("WARNING: MCP_API_KEY is not set — server is unauthenticated")
    uvicorn.run(starlette_app, host="0.0.0.0", port=port)
