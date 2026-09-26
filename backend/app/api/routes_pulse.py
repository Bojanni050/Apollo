"""AI Pulse endpoints.

A Pulse run walks the documentation repository with the *background* model
and proposes (or, in apply mode, writes) thematic tags and cross-document
connections. As with the inventory: a run never modifies anything on its own
in suggest mode; every write goes through the guarded proposal write path and
an explicit human approval.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_documentation_repository, get_workspace, resolve_repo_root
from app.db import get_db
from app.llm import get_provider
from app.llm.base import LLMError, LLMNotConfigured
from app.models import PulseItem, PulseRun, WorkspacePulseSettings
from app.schemas import (
    PulseApplyOut,
    PulseApplyRequest,
    PulseDecideOut,
    PulseItemOut,
    PulseRunOut,
    PulseRunRequest,
    PulseSettingsOut,
    PulseSettingsUpdate,
)
from app.services.pulse import PulseError, apply_pulse_item, run_pulse

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["pulse"])


def _run_out(run: PulseRun) -> PulseRunOut:
    out = PulseRunOut.model_validate(run)
    out.items = [PulseItemOut.model_validate(i) for i in run.items]
    return out


def _get_run(db: Session, workspace_id: int, run_id: int) -> PulseRun:
    run = db.scalar(
        select(PulseRun)
        .options(selectinload(PulseRun.items))
        .where(PulseRun.id == run_id, PulseRun.workspace_id == workspace_id)
    )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pulse run not found.")
    return run


def _get_settings(db: Session, workspace_id: int) -> WorkspacePulseSettings:
    """The workspace's Delphi Pulse settings row, created on first use."""
    row = db.get(WorkspacePulseSettings, workspace_id)
    if row is None:
        row = WorkspacePulseSettings(
            workspace_id=workspace_id,
            mode="suggest",
            schedule_enabled=False,
            schedule_kind="interval",
            interval_hours=1,
            weekly_day=0,
            weekly_hour=0,
        )
        db.add(row)
        db.flush()
    return row


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


@router.get("/pulse/settings", response_model=PulseSettingsOut)
def get_pulse_settings(workspace_id: int, db: Session = Depends(get_db)) -> PulseSettingsOut:
    get_workspace(db, workspace_id)
    return PulseSettingsOut.model_validate(_get_settings(db, workspace_id))


@router.put("/pulse/settings", response_model=PulseSettingsOut)
def update_pulse_settings(
    workspace_id: int,
    payload: PulseSettingsUpdate,
    db: Session = Depends(get_db),
) -> PulseSettingsOut:
    """Update the mode and/or the automatic scan schedule.

    Pydantic already bounds the values (hours 1-24, weekday 0-6, hour 0-23);
    this endpoint only persists them. The scheduler picks the new values up
    on its next check, typically within a minute.
    """
    get_workspace(db, workspace_id)
    row = _get_settings(db, workspace_id)
    row.mode = payload.mode
    row.schedule_enabled = payload.schedule_enabled
    row.schedule_kind = payload.schedule_kind
    row.interval_hours = payload.interval_hours
    row.weekly_day = payload.weekly_day
    row.weekly_hour = payload.weekly_hour
    db.commit()
    db.refresh(row)
    return PulseSettingsOut.model_validate(row)


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


@router.post("/pulse/runs", response_model=PulseRunOut, status_code=status.HTTP_201_CREATED)
def create_pulse_run(
    workspace_id: int, payload: PulseRunRequest, db: Session = Depends(get_db)
) -> PulseRunOut:
    """Scan the documentation for tags and connections.

    Suggest mode records proposals only; apply mode writes them into the
    documents as front matter. The mode is the workspace's configured mode,
    never a per-request choice, so a client cannot trigger writes the user
    did not opt into.
    """
    get_workspace(db, workspace_id)
    repo = get_documentation_repository(db, workspace_id)
    if payload.repository_id is not None and payload.repository_id != repo.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Pulse only applies to the documentation repository.",
        )
    root = resolve_repo_root(repo)
    mode = _get_settings(db, workspace_id).mode

    try:
        # The background role on purpose: Pulse is bulk classification, the
        # cheap-and-compact tier. The interactive chat keeps the strong model.
        provider = get_provider(role="background")
    except LLMNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    try:
        run = run_pulse(provider, db, workspace_id, root, mode=mode)
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM error: {exc}") from exc

    db.commit()
    db.refresh(run)
    return _run_out(run)


@router.get("/pulse/runs", response_model=list[PulseRunOut])
def list_pulse_runs(workspace_id: int, db: Session = Depends(get_db)) -> list[PulseRunOut]:
    get_workspace(db, workspace_id)
    runs = db.scalars(
        select(PulseRun)
        .options(selectinload(PulseRun.items))
        .where(PulseRun.workspace_id == workspace_id)
        .order_by(PulseRun.id.desc())
    ).all()
    return [_run_out(r) for r in runs]


@router.get("/pulse/runs/{run_id}", response_model=PulseRunOut)
def read_pulse_run(
    workspace_id: int, run_id: int, db: Session = Depends(get_db)
) -> PulseRunOut:
    return _run_out(_get_run(db, workspace_id, run_id))


# --------------------------------------------------------------------------
# Approval -- the only path from a suggestion to a written file (in suggest
# mode; apply mode wrote them during the run itself).
# --------------------------------------------------------------------------


@router.post("/pulse/runs/{run_id}/apply", response_model=PulseApplyOut)
def apply_pulse_run(
    workspace_id: int,
    run_id: int,
    payload: PulseApplyRequest,
    db: Session = Depends(get_db),
) -> PulseApplyOut:
    """Approve selected suggestions (or all of them) and write them to disk."""
    run = _get_run(db, workspace_id, run_id)
    repo = get_documentation_repository(db, workspace_id)
    root = resolve_repo_root(repo)

    wanted = set(payload.item_ids)
    applied: list[str] = []
    skipped: list[dict[str, str]] = []

    for item in run.items:
        if item.decision != "pending":
            skipped.append({"path": item.file_path, "reason": f"already {item.decision}"})
            continue
        if wanted and item.id not in wanted:
            skipped.append({"path": item.file_path, "reason": "not selected"})
            continue
        try:
            applied.append(apply_pulse_item(root, item))
        except PulseError as exc:
            skipped.append({"path": item.file_path, "reason": str(exc)})

    db.commit()
    return PulseApplyOut(run_id=run.id, applied=applied, skipped=skipped)


@router.post(
    "/pulse/runs/{run_id}/items/{item_id}/apply", response_model=PulseDecideOut
)
def apply_pulse_item_route(
    workspace_id: int, run_id: int, item_id: int, db: Session = Depends(get_db)
) -> PulseDecideOut:
    """Approve a single suggestion: tags and connections written to disk."""
    run = _get_run(db, workspace_id, run_id)
    item = next((i for i in run.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pulse item not found.")
    if item.decision != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This item is already {item.decision}."
        )

    repo = get_documentation_repository(db, workspace_id)
    root = resolve_repo_root(repo)
    try:
        applied_path = apply_pulse_item(root, item)
    except PulseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    db.refresh(item)
    return PulseDecideOut(
        run_id=run.id, item=PulseItemOut.model_validate(item), applied_path=applied_path
    )


@router.post(
    "/pulse/runs/{run_id}/items/{item_id}/skip", response_model=PulseDecideOut
)
def skip_pulse_item(
    workspace_id: int, run_id: int, item_id: int, db: Session = Depends(get_db)
) -> PulseDecideOut:
    """Decline a single suggestion. The filesystem is untouched."""
    run = _get_run(db, workspace_id, run_id)
    item = next((i for i in run.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pulse item not found.")
    if item.decision != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This item is already {item.decision}."
        )
    item.decision = "skipped"
    db.commit()
    db.refresh(item)
    return PulseDecideOut(run_id=run.id, item=PulseItemOut.model_validate(item))
