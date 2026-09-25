"""Document inventory endpoints.

An inventory run produces a *plan*. It never moves anything. The user accepts
individual suggestions or the whole plan, and only then is a document moved --
through the same guarded code path as any other proposal.
"""
from __future__ import annotations

import datetime as dt
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_documentation_repository, get_workspace, resolve_repo_root
from app.db import get_db
from app.llm import get_provider
from app.llm.base import LLMError, LLMNotConfigured
from app.models import InventoryItem, InventoryRun
from app.schemas import (
    InventoryApplyOut,
    InventoryApplyRequest,
    InventoryDecideOut,
    InventoryItemOut,
    InventoryRunOut,
    InventoryRunRequest,
)
from app.services.inventory import run_inventory
from app.services.proposals import ProposalError, apply_change, plan_move

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["inventory"])


def _item_out(item: InventoryItem) -> InventoryItemOut:
    """Render an item, including whether it would actually move anything."""
    out = InventoryItemOut.model_validate(item)
    source_dir = PurePosixPath(item.source_path).parent.as_posix()
    if item.suggested_path and source_dir != item.suggested_path:
        out.target_path = f"{item.suggested_path}/{PurePosixPath(item.source_path).name}"
        out.needs_move = True
    return out


def _run_out(run: InventoryRun) -> InventoryRunOut:
    out = InventoryRunOut.model_validate(run)
    out.items = [_item_out(i) for i in run.items]
    return out


def _get_run(db: Session, workspace_id: int, run_id: int) -> InventoryRun:
    run = db.scalar(
        select(InventoryRun)
        .options(selectinload(InventoryRun.items))
        .where(InventoryRun.id == run_id, InventoryRun.workspace_id == workspace_id)
    )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory run not found.")
    return run


@router.post("/inventory/runs", response_model=InventoryRunOut, status_code=status.HTTP_201_CREATED)
def create_inventory_run(
    workspace_id: int, payload: InventoryRunRequest, db: Session = Depends(get_db)
) -> InventoryRunOut:
    """Read every document and propose where it belongs. Moves nothing."""
    get_workspace(db, workspace_id)
    repo = get_documentation_repository(db, workspace_id)
    if payload.repository_id is not None and payload.repository_id != repo.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Inventory only applies to the documentation repository.",
        )
    root = resolve_repo_root(repo)

    try:
        provider = get_provider()
    except LLMNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    try:
        result = run_inventory(provider, root, payload.path)
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM error: {exc}") from exc

    run = InventoryRun(
        workspace_id=workspace_id,
        status="completed",
        summary=result.summary or None,
    )
    db.add(run)
    db.flush()

    for classification in result.classifications:
        db.add(
            InventoryItem(
                run_id=run.id,
                source_path=classification.path,
                purpose=classification.purpose,
                suggested_path=classification.suggested_path,
                confidence=classification.confidence,
                overlaps=classification.overlaps,
                ambiguous=classification.ambiguous,
                note=classification.note,
                decision="pending",
            )
        )
    db.commit()
    db.refresh(run)
    return _run_out(run)


@router.get("/inventory/runs", response_model=list[InventoryRunOut])
def list_inventory_runs(workspace_id: int, db: Session = Depends(get_db)) -> list[InventoryRunOut]:
    get_workspace(db, workspace_id)
    runs = db.scalars(
        select(InventoryRun)
        .options(selectinload(InventoryRun.items))
        .where(InventoryRun.workspace_id == workspace_id)
        .order_by(InventoryRun.id.desc())
    ).all()
    return [_run_out(r) for r in runs]


@router.get("/inventory/runs/{run_id}", response_model=InventoryRunOut)
def read_inventory_run(
    workspace_id: int, run_id: int, db: Session = Depends(get_db)
) -> InventoryRunOut:
    return _run_out(_get_run(db, workspace_id, run_id))



# --------------------------------------------------------------------------
# Approval -- the only path from a plan to a moved file.
# --------------------------------------------------------------------------


def _decide_item(
    db: Session,
    workspace_id: int,
    item: InventoryItem,
    root: str,
    apply_it: bool,
) -> tuple[str | None, str | None]:
    """Apply or skip one inventory suggestion. Returns (applied_path, error)."""
    if not apply_it:
        item.decision = "skipped"
        return None, None

    source_dir = PurePosixPath(item.source_path).parent.as_posix()
    if not item.suggested_path or item.suggested_path == source_dir:
        # Already in the right place: nothing to move, nothing to record.
        item.decision = "skipped"
        return None, None

    try:
        # The inventory may file a document into a folder of the target
        # structure that does not exist yet; apply_change creates it.
        change = plan_move(root, item.source_path, item.suggested_path, allow_missing_dir=True)
        applied = apply_change(root, change)
    except ProposalError as exc:
        # Leave the item pending so the user can see it failed and retry.
        return None, str(exc)

    item.decision = "applied"
    return applied, None


@router.post(
    "/inventory/runs/{run_id}/apply", response_model=InventoryApplyOut
)
def apply_inventory_run(
    workspace_id: int,
    run_id: int,
    payload: InventoryApplyRequest,
    db: Session = Depends(get_db),
) -> InventoryApplyOut:
    """Approve selected suggestions (or all of them) and perform the moves.

    An empty ``item_ids`` approves the whole plan. Ambiguous items are never
    applied automatically -- they must be resolved by hand.
    """
    run = _get_run(db, workspace_id, run_id)
    repo = get_documentation_repository(db, workspace_id)
    root = resolve_repo_root(repo)

    wanted = set(payload.item_ids)
    applied: list[str] = []
    skipped: list[dict[str, str]] = []

    for item in run.items:
        if item.decision != "pending":
            skipped.append({"path": item.source_path, "reason": f"already {item.decision}"})
            continue
        if wanted and item.id not in wanted:
            skipped.append({"path": item.source_path, "reason": "not selected"})
            continue
        if item.ambiguous:
            skipped.append({"path": item.source_path, "reason": "ambiguous - needs a human"})
            continue

        applied_path, error = _decide_item(db, workspace_id, item, root, apply_it=True)
        if error:
            skipped.append({"path": item.source_path, "reason": error})
        elif applied_path:
            applied.append(applied_path)
        else:
            skipped.append({"path": item.source_path, "reason": "already in place"})

    if applied:
        run.applied_at = dt.datetime.now(dt.timezone.utc)
    db.commit()

    return InventoryApplyOut(run_id=run.id, applied=applied, skipped=skipped)


@router.post(
    "/inventory/runs/{run_id}/items/{item_id}/apply", response_model=InventoryDecideOut
)
def apply_inventory_item(
    workspace_id: int, run_id: int, item_id: int, db: Session = Depends(get_db)
) -> InventoryDecideOut:
    """Approve a single suggestion. Ambiguous items are refused here too."""
    run = _get_run(db, workspace_id, run_id)
    item = next((i for i in run.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory item not found.")
    if item.decision != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This item is already {item.decision}."
        )
    if item.ambiguous:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This document is ambiguous. Re-run the inventory or move it by hand.",
        )

    repo = get_documentation_repository(db, workspace_id)
    root = resolve_repo_root(repo)
    applied_path, error = _decide_item(db, workspace_id, item, root, apply_it=True)
    if error:
        raise HTTPException(status.HTTP_409_CONFLICT, error)
    db.commit()
    db.refresh(item)
    return InventoryDecideOut(run_id=run.id, item=_item_out(item), applied_path=applied_path)


@router.post(
    "/inventory/runs/{run_id}/items/{item_id}/skip", response_model=InventoryDecideOut
)
def skip_inventory_item(
    workspace_id: int, run_id: int, item_id: int, db: Session = Depends(get_db)
) -> InventoryDecideOut:
    """Decline a single suggestion. The filesystem is untouched."""
    run = _get_run(db, workspace_id, run_id)
    item = next((i for i in run.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory item not found.")
    if item.decision != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This item is already {item.decision}."
        )
    item.decision = "skipped"
    db.commit()
    db.refresh(item)
    return InventoryDecideOut(run_id=run.id, item=_item_out(item))
