"""System helper endpoints for folder picking and filesystem browsing."""
from __future__ import annotations

import os
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.config import settings
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
