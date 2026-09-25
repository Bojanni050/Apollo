"""Gaia Docs Architect API.

A standalone workspace for exploring, discussing and maintaining Gaia's
architecture documentation. The app is independent of Gaia's runtime: it only
reads and writes Markdown files in repositories you register, and stores its
own state in PostgreSQL.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    routes_chat,
    routes_documents,
    routes_inventory,
    routes_proposals,
    routes_workspaces,
)
from app.config import settings
from app.db import init_db

logger = logging.getLogger("gaia_docs_architect")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "AI-powered documentation and architecture workspace for the Gaia ecosystem."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}


app.include_router(routes_workspaces.router, prefix="/api")
app.include_router(routes_documents.router, prefix="/api")
app.include_router(routes_proposals.router, prefix="/api")
app.include_router(routes_chat.router, prefix="/api")
app.include_router(routes_inventory.router, prefix="/api")
