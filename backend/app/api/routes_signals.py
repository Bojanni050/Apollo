"""What Delphi makes of a collection: the analysis, and the findings it leaves.

Three endpoints, and not one of them writes a file:

* ``POST /delphi/analyze``       -- the one button. Reads the collection, records
  what stands out, and answers in a sentence the reader can act on.
* ``GET  /signals``              -- the findings for a document, or for a group.
* ``POST /signals/{id}/dismiss`` -- hide one finding.

The analysis is a read with a note-taking habit. It opens documents, asks the
background model, and stores what it found; there is no code path from a finding
to a file operation, and the only way a document ever changes is the proposal
flow. The route table says so too: the test that checks it reads this file's
routes and asserts there is nothing here that can move or remove a document.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_analysis_repository,
    get_repository,
    get_workspace,
    resolve_repo_root,
)
from app.db import get_db
from app.llm import get_provider
from app.llm.base import LLMError, LLMNotConfigured
from app.models import DocSignal, Group
from app.schemas import (
    AnalyseOut,
    AnalyseRequest,
    GroupProposalOut,
    OpenSignalsOut,
    SignalOut,
)
from app.services.delphi import (
    DelphiError,
    analyse,
    dismiss_signal,
    open_signal_count,
    record_signals,
    signals_for_document,
    signals_for_group,
)
from app.services.delphi_grouping import propose_clusters, propose_groups
from app.services.placement import PlacementError, get_group
from app.services.signals import SIGNAL_LABELS

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["signals"])


def _signal_out(signal: DocSignal) -> SignalOut:
    """One finding as the reader sees it.

    Built field by field rather than from the ORM object, because the label is
    not a column: it is the reader-facing wording of ``kind`` and lives in one
    place, so it can be improved without touching what is stored.
    """
    return SignalOut(
        id=signal.id,
        repository_id=signal.repository_id,
        file_path=signal.file_path,
        kind=signal.kind,
        label=SIGNAL_LABELS.get(signal.kind, signal.kind),
        reference=signal.reference,
        why=signal.why,
        confidence=signal.confidence,
        status=signal.status,
        created_at=signal.created_at,
    )


@router.post("/delphi/analyze", response_model=AnalyseOut)
def analyze_collection(
    workspace_id: int,
    payload: AnalyseRequest | None = None,
    db: Session = Depends(get_db),
) -> AnalyseOut:
    """Read the collection and record what stands out.

    Synchronous on purpose, and deliberately dull about the alternative: a
    collection of ten documents is one request, and a button that returns when the
    answer is ready is a button the reader trusts.

    Without a configured model this refuses with 503 rather than pretending to
    have found nothing, because "nothing stood out" and "nobody looked" must not
    read the same way.
    """
    get_workspace(db, workspace_id)
    repo = get_analysis_repository(db, workspace_id)
    if payload is not None and payload.repository_id is not None:
        requested = get_repository(db, workspace_id, payload.repository_id)
        if not (requested.is_documentation or requested.is_storage):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Delphi analyses documents, not source repositories. A source "
                "repository holds code, and judging whether code is out of date "
                "is a different question.",
            )
        repo = requested
    root = resolve_repo_root(repo)

    try:
        # The background role, like every other bulk pass: classification over a
        # whole corpus is the cheap-and-compact tier's job. The interactive chat
        # keeps the strong model.
        provider = get_provider(role="background")
    except LLMNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    try:
        result = analyse(provider, root)
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM error: {exc}") from exc

    stored = record_signals(db, workspace_id, repo.id, result.signals)

    # The grouping pass runs on the findings, not on a second reading of the
    # documents, so a cluster spanning two reading batches is still visible. It
    # is a second request, and it is allowed to fail on its own: the findings are
    # already recorded and are worth the reader's attention whether or not a
    # group could be proposed for them.
    #
    # The workspace's existing areas (hoofdgebieden) are named for the prompt
    # so Delphi prefers one of them over inventing a new one -- an area is a
    # group with a folder and no parent of its own.
    areas = list(
        db.scalars(
            select(Group.name).where(
                Group.workspace_id == workspace_id,
                Group.folder.is_not(None),
                Group.parent_group_id.is_(None),
            )
        ).all()
    )
    proposals = propose_groups(
        db,
        workspace_id,
        repo.id,
        propose_clusters(provider, result.signals, result.paths, areas),
    )

    return AnalyseOut(
        repository_id=repo.id,
        documents=result.documents,
        analysed=result.analysed,
        signals=[_signal_out(s) for s in stored],
        open_signals=open_signal_count(db, workspace_id),
        groups=[
            GroupProposalOut(
                group_id=p.group_id,
                name=p.name,
                placed=p.placed,
                left_alone=p.left_alone,
                unavailable=p.unavailable,
            )
            for p in proposals
        ],
        summary=result.summary,
        errors=result.errors,
    )


@router.get("/signals/count", response_model=OpenSignalsOut)
def count_signals(workspace_id: int, db: Session = Depends(get_db)) -> OpenSignalsOut:
    """How many findings are waiting for a decision in this workspace.

    Read on every workspace change rather than remembered from the last
    analysis: a badge that says zero because nothing has been analysed yet,
    while three findings are open, is the exact kind of quiet lie this
    application exists to avoid.
    """
    get_workspace(db, workspace_id)
    return OpenSignalsOut(open_signals=open_signal_count(db, workspace_id))


@router.get("/signals", response_model=list[SignalOut])
def read_signals(
    workspace_id: int,
    repository_id: int | None = Query(
        default=None,
        description="Required with path: a path is not a document on its own",
    ),
    path: str | None = Query(default=None, description="Repository-relative path"),
    group_id: int | None = Query(default=None, description="A visual group, for its panel"),
    include_dismissed: bool = False,
    db: Session = Depends(get_db),
) -> list[SignalOut]:
    """The findings for one document, or for everything in one group.

    Exactly one of ``path`` and ``group_id``. Asking for neither would mean the
    whole workspace, which is a different question and would drown the reading
    pane it is meant to sit above; asking for both has no answer.
    """
    get_workspace(db, workspace_id)
    if (path is None) == (group_id is None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ask for either one document (repository_id and path) or one group "
            "(group_id), not both and not neither.",
        )
    if group_id is not None:
        # Resolved through the same service the board uses, so an id from another
        # workspace is refused rather than quietly returning the findings of
        # somebody else's group.
        try:
            get_group(db, workspace_id, group_id)
        except PlacementError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        found = signals_for_group(
            db, workspace_id, group_id, include_dismissed=include_dismissed
        )
    else:
        if repository_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "A path is only a document together with the repository it is in.",
            )
        get_repository(db, workspace_id, repository_id)
        found = signals_for_document(
            db,
            workspace_id,
            repository_id,
            path or "",
            include_dismissed=include_dismissed,
        )
    return [_signal_out(s) for s in found]


@router.post("/signals/{signal_id}/dismiss", response_model=SignalOut)
def dismiss(workspace_id: int, signal_id: int, db: Session = Depends(get_db)) -> SignalOut:
    """Hide one finding.

    The document is not touched, and the decision survives the next analysis:
    dismissal is a statement about the finding, not a temporary mute.
    """
    get_workspace(db, workspace_id)
    try:
        signal = dismiss_signal(db, workspace_id, signal_id)
    except DelphiError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _signal_out(signal)

