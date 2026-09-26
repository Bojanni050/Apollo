"""System helper endpoints for folder picking and filesystem browsing."""
from __future__ import annotations

import os
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ValidationError

from app.config import BACKEND_DIR, settings
from app.services.paths import PathSecurityError, assert_authorized_root

router = APIRouter(prefix="/system", tags=["system"])


class FolderItem(BaseModel):
    name: str
    path: str
    is_dir: bool = True


class QuickAccessItem(BaseModel):
    name: str
    path: str


class FolderBrowseResponse(BaseModel):
    current_path: str
    parent_path: str | None
    folders: list[FolderItem]
    drives: list[str]
    quick_access: list[QuickAccessItem]


class NativePickResponse(BaseModel):
    path: str | None = None
    cancelled: bool = False
    error: str | None = None


#: The LLM settings the UI may read and write. Everything else in .env stays
#: untouchable from the browser.
LLM_ENV_KEYS = (
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_CONTEXT_TOKENS",
    "LLM_MAX_OUTPUT_TOKENS",
    "LLM_TEMPERATURE",
    "BACKGROUND_LLM_BASE_URL",
    "BACKGROUND_LLM_MODEL",
    "BACKGROUND_LLM_API_KEY",
    "BACKGROUND_LLM_CONTEXT_TOKENS",
    "BACKGROUND_LLM_MAX_OUTPUT_TOKENS",
)


class LlmSettingsResponse(BaseModel):
    base_url: str | None
    model: str | None
    context_tokens: int
    max_output_tokens: int
    temperature: float
    api_key_configured: bool
    background_base_url: str | None = None
    background_model: str | None = None
    background_context_tokens: int | None = None
    background_max_output_tokens: int | None = None
    background_api_key_configured: bool = False


class LlmSettingsUpdate(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    context_tokens: int | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    background_base_url: str | None = None
    background_model: str | None = None
    background_api_key: str | None = None
    background_context_tokens: int | None = None
    background_max_output_tokens: int | None = None


class ModelInfo(BaseModel):
    id: str
    context_window: int | None = None
    owned_by: str | None = None
    input_price_per_m: float | None = None
    output_price_per_m: float | None = None
    capabilities: list[str] = []


class ListModelsRequest(BaseModel):
    # Optional so the settings screen can fetch with the values typed in the
    # form before they are saved; when absent the saved settings are used.
    base_url: str | None = None
    api_key: str | None = None


class ListModelsResponse(BaseModel):
    models: list[ModelInfo]
    error: str | None = None


#: Best-effort metadata for well-known model families. The /models endpoint of
#: the OpenAI protocol does not carry pricing or capabilities, so known ids are
#: enriched here and everything else is returned as-is with null pricing.
#: Prices are USD per 1M tokens, as the vendors publish them.
_KNOWN_MODELS: dict[str, dict[str, Any]] = {
    "gpt-4o": {"in": 2.5, "out": 10.0, "caps": ["vision", "tools"]},
    "gpt-4o-mini": {"in": 0.15, "out": 0.6, "caps": ["vision", "tools"]},
    "gpt-4.1": {"in": 2.0, "out": 8.0, "caps": ["vision", "tools"]},
    "gpt-4.1-mini": {"in": 0.4, "out": 1.6, "caps": ["vision", "tools"]},
    "gpt-4.1-nano": {"in": 0.1, "out": 0.4, "caps": ["vision", "tools"]},
    "o3": {"in": 2.0, "out": 8.0, "caps": ["vision", "tools", "reasoning"]},
    "o4-mini": {"in": 1.1, "out": 4.4, "caps": ["vision", "tools", "reasoning"]},
    "gpt-5": {"in": 1.25, "out": 10.0, "caps": ["vision", "tools", "reasoning"]},
    "claude-opus-4": {"in": 15.0, "out": 75.0, "caps": ["vision", "tools", "reasoning"]},
    "claude-sonnet-4": {"in": 3.0, "out": 15.0, "caps": ["vision", "tools", "reasoning"]},
    "claude-haiku": {"in": 0.8, "out": 4.0, "caps": ["vision", "tools"]},
    "gemini-2.5-pro": {"in": 1.25, "out": 10.0, "caps": ["vision", "tools", "reasoning"]},
    "gemini-2.5-flash": {"in": 0.3, "out": 2.5, "caps": ["vision", "tools"]},
    "deepseek-chat": {"in": 0.27, "out": 1.1, "caps": ["tools"]},
    "deepseek-reasoner": {"in": 0.55, "out": 2.19, "caps": ["tools", "reasoning"]},
    "llama": {"in": None, "out": None, "caps": ["tools"]},
    "mistral": {"in": None, "out": None, "caps": ["tools"]},
    "qwen": {"in": None, "out": None, "caps": ["tools"]},
}


def _model_metadata(model_id: str) -> tuple[float | None, float | None, list[str]]:
    """Match the longest known family name contained in the model id."""
    lower = model_id.lower()
    best: dict[str, Any] | None = None
    best_len = 0
    for family, info in _KNOWN_MODELS.items():
        if family in lower and len(family) > best_len:
            best = info
            best_len = len(family)
    if best is None:
        return None, None, []
    return best["in"], best["out"], list(best["caps"])


@router.post("/settings/llm/models", response_model=ListModelsResponse)
def list_llm_models(request: ListModelsRequest | None = None) -> ListModelsResponse:
    """Fetch the model catalogue from the configured (or given) endpoint.

    Uses the form values when provided, so a user can point at a provider and
    list its models before saving. The key given here is used for this request
    only and never stored.
    """
    from app.llm.base import LLMError
    from app.llm.openai_compat import OpenAICompatibleProvider

    body = request or ListModelsRequest()
    base_url = (body.base_url or settings.llm_base_url or "").strip()
    api_key = (body.api_key or settings.llm_api_key or "").strip() or None
    if not base_url:
        return ListModelsResponse(models=[], error="Set a base URL first.")

    try:
        # The provider constructor requires a model for chat; listing models
        # must work before one is chosen, so a placeholder is passed. It is
        # never used by list_models and never persisted.
        provider = OpenAICompatibleProvider(base_url=base_url, api_key=api_key, model="list-models")
        raw = provider.list_models()
    except LLMError as exc:
        return ListModelsResponse(models=[], error=str(exc))

    models = [
        ModelInfo(
            id=m["id"],
            context_window=m.get("context_window"),
            owned_by=m.get("owned_by"),
            input_price_per_m=(_model_metadata(m["id"])[0]),
            output_price_per_m=(_model_metadata(m["id"])[1]),
            capabilities=_model_metadata(m["id"])[2],
        )
        for m in raw
    ]
    models.sort(key=lambda m: m.id.lower())
    return ListModelsResponse(models=models)


def _apply_llm_settings(update: LlmSettingsUpdate) -> None:
    """Validate and apply an update, both to the live settings and to .env.

    The live object is updated first, so validation errors surface as 400s
    before anything is written to disk. The .env write makes the change
    survive a restart without editing the file by hand. Assignment on a
    pydantic BaseSettings instance runs the field validators, so an
    out-of-range value is rejected here rather than poisoning the next boot.
    """
    try:
        if update.base_url is not None:
            settings.llm_base_url = update.base_url.strip() or None
        if update.model is not None:
            settings.llm_model = update.model.strip() or None
        if update.api_key is not None:
            settings.llm_api_key = update.api_key.strip() or None
        if update.context_tokens is not None:
            settings.llm_context_tokens = update.context_tokens
        if update.max_output_tokens is not None:
            settings.llm_max_output_tokens = update.max_output_tokens
        if update.temperature is not None:
            settings.llm_temperature = update.temperature
        if update.background_base_url is not None:
            settings.background_llm_base_url = update.background_base_url.strip() or None
        if update.background_model is not None:
            settings.background_llm_model = update.background_model.strip() or None
        if update.background_api_key is not None:
            settings.background_llm_api_key = update.background_api_key.strip() or None
        if update.background_context_tokens is not None:
            settings.background_llm_context_tokens = update.background_context_tokens
        if update.background_max_output_tokens is not None:
            settings.background_llm_max_output_tokens = update.background_max_output_tokens
        # "Same as chat model": with no background model the background role
        # resolves to the primary provider, so stale background limits must
        # not linger and silently shrink its context budget.
        if not settings.background_llm_model:
            settings.background_llm_context_tokens = None
            settings.background_llm_max_output_tokens = None
        settings._validate_llm_context()
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    _write_llm_env()


def _write_llm_env() -> None:
    """Persist the LLM keys to backend/.env, preserving every other line."""
    env_path = BACKEND_DIR / ".env"
    values = {
        "LLM_BASE_URL": settings.llm_base_url or "",
        "LLM_MODEL": settings.llm_model or "",
        "LLM_API_KEY": settings.llm_api_key or "",
        "LLM_CONTEXT_TOKENS": str(settings.llm_context_tokens),
        "LLM_MAX_OUTPUT_TOKENS": str(settings.llm_max_output_tokens),
        "LLM_TEMPERATURE": str(settings.llm_temperature),
        "BACKGROUND_LLM_BASE_URL": settings.background_llm_base_url or "",
        "BACKGROUND_LLM_MODEL": settings.background_llm_model or "",
        "BACKGROUND_LLM_API_KEY": settings.background_llm_api_key or "",
        "BACKGROUND_LLM_CONTEXT_TOKENS": (
            str(settings.background_llm_context_tokens) if settings.background_llm_context_tokens else ""
        ),
        "BACKGROUND_LLM_MAX_OUTPUT_TOKENS": (
            str(settings.background_llm_max_output_tokens) if settings.background_llm_max_output_tokens else ""
        ),
    }

    lines: list[str] = []
    seen: set[str] = set()
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            key = raw.split("=", 1)[0].strip()
            if key in LLM_ENV_KEYS:
                if key not in seen:
                    lines.append(f"{key}={values[key]}")
                    seen.add(key)
            else:
                lines.append(raw)
    for key in LLM_ENV_KEYS:
        if key not in seen:
            lines.append(f"{key}={values[key]}")
            seen.add(key)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.get("/settings/llm", response_model=LlmSettingsResponse)
def get_llm_settings() -> LlmSettingsResponse:
    """Current LLM configuration. The API key is never returned -- only whether
    one is configured -- so the settings screen can show status without ever
    exposing the secret to the browser."""
    return LlmSettingsResponse(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        context_tokens=settings.llm_context_tokens,
        max_output_tokens=settings.llm_max_output_tokens,
        temperature=settings.llm_temperature,
        api_key_configured=bool(settings.llm_api_key),
        background_base_url=settings.background_llm_base_url,
        background_model=settings.background_llm_model,
        background_context_tokens=settings.background_llm_context_tokens,
        background_max_output_tokens=settings.background_llm_max_output_tokens,
        background_api_key_configured=bool(settings.background_llm_api_key),
    )


@router.put("/settings/llm", response_model=LlmSettingsResponse)
def update_llm_settings(update: LlmSettingsUpdate) -> LlmSettingsResponse:
    """Update the LLM configuration and persist it to backend/.env. Route-level
    authentication is enforced by the middleware for every /api route; in
    production with auth enabled an unauthenticated caller never gets here."""
    _apply_llm_settings(update)
    return get_llm_settings()


def _get_system_drives() -> list[str]:
    """Return available system drives on Windows or root on POSIX."""
    drives: list[str] = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive_path = f"{letter}:\\"
            if os.path.exists(drive_path):
                drives.append(drive_path)
    else:
        drives.append("/")
    return drives


def _get_quick_access() -> list[QuickAccessItem]:
    """Return common user directories that exist on the system."""
    items: list[QuickAccessItem] = []
    try:
        home = Path.home().resolve()
        items.append(QuickAccessItem(name="Home", path=str(home)))

        docs = (home / "Documents").resolve()
        if docs.exists() and docs.is_dir():
            items.append(QuickAccessItem(name="Documents", path=str(docs)))

        desktop = (home / "Desktop").resolve()
        if desktop.exists() and desktop.is_dir():
            items.append(QuickAccessItem(name="Desktop", path=str(desktop)))

        cwd = Path.cwd().resolve()
        items.append(QuickAccessItem(name="Workspace Directory", path=str(cwd)))

        parent = cwd.parent.resolve()
        if parent != cwd and parent.exists():
            items.append(QuickAccessItem(name="Projects Directory", path=str(parent)))
    except Exception:
        pass
    return items


@router.get("/folders", response_model=FolderBrowseResponse)
def browse_folders(path: str | None = Query(None, description="Directory path to inspect")) -> FolderBrowseResponse:
    """List subdirectories of a path, drive roots, and quick access shortcuts."""
    if path and path.strip():
        raw_path = path.strip()
    else:
        # Default starting point: current working directory or home
        raw_path = str(Path.cwd().resolve())

    candidate = Path(raw_path).expanduser().resolve()

    # Enforce configured security roots if restricted mode is enabled
    if not settings.unrestricted_workspace_roots and settings.allowed_workspace_roots:
        try:
            candidate = assert_authorized_root(
                candidate,
                settings.allowed_workspace_roots,
                allow_unrestricted=False,
            )
        except PathSecurityError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    if not candidate.exists() or not candidate.is_dir():
        # Fallback to parent or home
        if candidate.parent.exists() and candidate.parent.is_dir():
            candidate = candidate.parent
        else:
            candidate = Path.home().resolve()

    folders: list[FolderItem] = []
    try:
        entries = sorted(candidate.iterdir(), key=lambda e: e.name.lower())
        for entry in entries:
            name = entry.name
            # Skip hidden, temporary, or OS-protected folders
            if (
                name.startswith(".")
                or name.startswith("$")
                or name in {
                    "System Volume Information",
                    "$RECYCLE.BIN",
                    "node_modules",
                    "__pycache__",
                    ".git",
                    ".venv",
                }
            ):
                continue
            try:
                if entry.is_dir():
                    folders.append(
                        FolderItem(
                            name=name,
                            path=str(entry.resolve()),
                            is_dir=True,
                        )
                    )
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError) as exc:
        # If directory itself is not readable, return empty list rather than 500
        pass

    parent_path = str(candidate.parent.resolve()) if candidate.parent != candidate else None

    return FolderBrowseResponse(
        current_path=str(candidate),
        parent_path=parent_path,
        folders=folders,
        drives=_get_system_drives(),
        quick_access=_get_quick_access(),
    )


@router.post("/pick-native-folder", response_model=NativePickResponse)
def pick_native_folder() -> NativePickResponse:
    """Trigger the host OS native folder picker dialog if running locally."""
    try:
        # Run in a separate Python process to avoid blocking the server loop or Tkinter thread issues
        script = (
            "import tkinter as tk; from tkinter import filedialog; "
            "root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True); "
            "path = filedialog.askdirectory(); print(path)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        selected = result.stdout.strip()
        if not selected:
            return NativePickResponse(cancelled=True)
        return NativePickResponse(path=selected, cancelled=False)
    except subprocess.TimeoutExpired:
        return NativePickResponse(cancelled=True, error="Folder picker timed out.")
    except Exception as exc:
        return NativePickResponse(error=f"Could not open native folder dialog: {exc}")
