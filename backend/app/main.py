"""Apollo API.

A standalone workspace for exploring, discussing and maintaining Gaia's
architecture documentation. The app is independent of Gaia's runtime: it only
reads and writes Markdown files in repositories you register, and stores its
own state in PostgreSQL.

Request pipeline
----------------
Middleware runs outermost-first, so the order added here is the order a request
travels:

1. ``CORSMiddleware`` -- answers preflights and attaches headers, including to
   401 responses, so a browser can read *why* a request was refused.
2. ``AuthenticationMiddleware`` -- the security boundary. Every ``/api`` route
   is authenticated server-side; only the small public allowlist below is not.

Authentication is enforced here rather than per-route on purpose: a new route
added to a router is protected by default, and cannot be shipped unauthenticated
by forgetting a dependency.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import (
    routes_auth,
    routes_chat,
    routes_decisions,
    routes_documents,
    routes_groups,
    routes_inbox,
    routes_inventory,
    routes_proposals,
    routes_pulse,
    routes_questions,
    routes_signals,
    routes_sources,
    routes_system,
    routes_workspaces,
)
from app.config import Settings, settings
from app.db import init_db
from app.security import authenticate_request

logger = logging.getLogger("apollo")

#: Routes reachable without a session. Everything else under /api requires
#: authentication. Health is unauthenticated so that a load balancer or
#: container probe can reach it; it exposes no workspace data.
PUBLIC_API_PATHS = frozenset(
    {
        "/api/health",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/status",
    }
)

API_PREFIX = "/api"


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated API requests before they reach a route.

    Returns 401 with a ``WWW-Authenticate`` header, and never reveals whether a
    particular resource exists -- an unauthenticated caller learns nothing about
    workspaces, documents or conversations.
    """

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith(API_PREFIX + "/"):
            return await call_next(request)

        if request.url.path in PUBLIC_API_PATHS:
            return await call_next(request)

        if not settings.auth_enabled:
            # Only reachable in development: validate_security() refuses to
            # start a production server with authentication disabled.
            return await call_next(request)

        if authenticate_request(settings, request) is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail before binding the port: a production deployment with an unsafe
    # configuration must never begin serving requests.
    settings.validate_security()
    if settings.is_production:
        logger.info(
            "Starting %s in production mode: authentication enforced, "
            "%d allowed workspace root(s), CORS origins %s.",
            settings.app_name,
            len(settings.allowed_workspace_roots),
            settings.effective_cors_origins,
        )
    else:
        logger.warning(
            "Starting in DEVELOPMENT mode (APP_ENV=development). "
            "Authentication is %s.",
            "disabled" if not settings.auth_enabled else "enabled",
        )
    init_db()
    # Delphi Pulse background scheduler: runs scheduled scans for workspaces
    # that opted in. Daemon thread, stopped on shutdown.
    from app.services import pulse_scheduler

    pulse_scheduler.start()
    try:
        yield
    finally:
        pulse_scheduler.stop()


def create_app(config: Settings | None = None) -> FastAPI:
    """Build the application.

    ``config`` defaults to the process-wide settings. It is injectable so tests
    can assert CORS behaviour for a specific origin list without mutating
    global state.
    """
    config = config or settings

    application = FastAPI(
        title=config.app_name,
        version="0.1.0",
        description=(
            "AI-powered documentation and architecture workspace for the Gaia ecosystem."
        ),
        lifespan=lifespan,
    )

    # Added last => outermost, so CORS headers are present on 401 responses too.
    # allow_origins comes from configuration and is never a wildcard in
    # production: with credentials enabled, "*" would let any site issue
    # authenticated requests.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.effective_cors_origins,
        allow_credentials=config.cors_allow_credentials,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    application.add_middleware(AuthenticationMiddleware)

    @application.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": config.app_name, "version": "0.1.0"}

    application.include_router(routes_auth.router, prefix="/api")
    application.include_router(routes_workspaces.router, prefix="/api")
    application.include_router(routes_documents.router, prefix="/api")
    # The inbox before the groups: it is the only route that can create a
    # repository, and it is where the basis workflow starts -- documents get in
    # first, and everything else is a way of looking at them.
    application.include_router(routes_inbox.router, prefix="/api")
    # Groups before proposals: a group is a view over documents, and the
    # proposal flow is the only thing that moves a file. Keeping them apart here
    # makes the boundary obvious in the route table.
    application.include_router(routes_groups.router, prefix="/api")
    application.include_router(routes_proposals.router, prefix="/api")
    application.include_router(routes_chat.router, prefix="/api")
    application.include_router(routes_inventory.router, prefix="/api")
    # The analysis after the pulse: both read the same collection, and this one
    # answers a different question -- what stands out, rather than what relates
    # to what. Registered separately so the route table shows two passes rather
    # than one pass with two names.
    application.include_router(routes_signals.router, prefix="/api")
    application.include_router(routes_pulse.router, prefix="/api")
    application.include_router(routes_questions.router, prefix="/api")
    application.include_router(routes_decisions.router, prefix="/api")
    application.include_router(routes_sources.router, prefix="/api")
    application.include_router(routes_system.router, prefix="/api")

    return application


app = create_app()
