"""Application settings.

All configuration is environment-driven so the app stays independent of any
particular deployment. Nothing here references Gaia runtime services.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Gaia Docs Architect"
    debug: bool = True

    # PostgreSQL is the production store. A sqlite file is supported purely so
    # the app can be started and tested without a running database server.
    database_url: str = "postgresql+psycopg://gaia:gaia@localhost:5432/gaia_docs"

    # Origins allowed to call the API (the Vite dev server by default).
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Directories that may be registered as workspace repositories. Any file
    # access outside these roots is rejected (see services/paths.py).
    allowed_workspace_roots: list[str] = []

    # ---- LLM provider (OpenAI-compatible) -------------------------------
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_context_tokens: int = 128_000
    llm_max_output_tokens: int = 8_192
    llm_temperature: float = 0.2
    llm_timeout_seconds: int = 120


settings = Settings()
