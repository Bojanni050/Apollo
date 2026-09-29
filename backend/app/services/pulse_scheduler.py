"""Delphi Pulse background scheduler.

A daemon thread, started with the application, that periodically checks
every workspace's Pulse schedule and runs a scan when its slot has arrived:

* ``interval`` -- every N hours (1-24) since the last run;
* ``weekly`` -- once per week, on the chosen weekday at the chosen hour.

Runs go through the exact same code path as a manual scan (``run_pulse``),
so scheduling adds no second implementation to keep honest. A run failure is
recorded on the run itself and logged here; the scheduler keeps going.

The check interval (default 60s) is deliberately coarse: schedules are
hour-granular, so a minute of jitter is invisible.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Workspace, WorkspacePulseSettings

logger = logging.getLogger("apollo.pulse")

#: Seconds between schedule checks.
CHECK_INTERVAL_SECONDS = 60

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def due(
    settings: WorkspacePulseSettings, now: dt.datetime
) -> bool:
    """Whether this workspace's scheduled scan slot has arrived.

    Pure and timezone-safe: the stored ``last_run_at`` may be naive (SQLite
    caveat, see app.models), so both sides are normalized to aware UTC before
    comparing.
    """
    if not settings.schedule_enabled:
        return False

    last = settings.last_run_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    now = now.astimezone(dt.timezone.utc)

    if settings.schedule_kind == "interval":
        if last is None:
            return True
        return now >= last + dt.timedelta(hours=settings.interval_hours)

    # Weekly: the slot is the configured weekday+hour in the server's local
    # time, matching what the user chose in the UI ("every Monday at 9").
    if now.weekday() != settings.weekly_day:
        return False
    if now.hour != settings.weekly_hour:
        return False
    if last is None:
        return True
    return now - last >= dt.timedelta(hours=1)


def run_due_scans() -> int:
    """Run a Pulse scan for every workspace whose slot has arrived.

    Returns the number of scans started. Runs sequentially on purpose: the
    background model is one configured resource, and serial scans keep its
    load predictable.
    """
    from app.llm import get_provider
    from app.llm.base import LLMError
    from app.services.pulse import run_pulse

    started = 0
    db = SessionLocal()
    try:
        rows = db.scalars(select(WorkspacePulseSettings)).all()
        now = dt.datetime.now(dt.timezone.utc)
        for row in rows:
            if not due(row, now):
                continue
            workspace_id = row.workspace_id
            ws = db.get(Workspace, workspace_id)
            if ws is None:
                continue
            # The scheduled scan sees the same trees a manual one does:
            # the documentation repository and the inbox storage (documents
            # wait in the inbox to be analysed, and a scheduled pass that
            # skipped them would analyse nothing on a fresh workspace).
            doc_repo = next(
                (r for r in ws.repositories if r.kind == "documentation" and not r.is_storage),
                None,
            )
            if doc_repo is None:
                continue
            roots: list[tuple[int | None, str]] = [(doc_repo.id, str(doc_repo.local_path))]
            storage = next(
                (r for r in ws.repositories if r.is_storage),
                None,
            )
            if storage is not None and storage.id != doc_repo.id:
                roots.append((storage.id, str(storage.local_path)))
            try:
                provider = get_provider(role="background")
                run = run_pulse(provider, db, workspace_id, roots, mode=row.mode)
                db.commit()
                started += 1
                logger.info(
                    "Delphi Pulse scheduled scan for workspace %s: %s (%s).",
                    workspace_id,
                    run.status,
                    run.summary,
                )
            except LLMError as exc:
                logger.warning(
                    "Delphi Pulse scheduled scan for workspace %s failed: %s",
                    workspace_id,
                    exc,
                )
                db.rollback()
            row.last_run_at = now
            db.commit()
    finally:
        db.close()
    return started


def _loop() -> None:
    while not _stop_event.wait(CHECK_INTERVAL_SECONDS):
        try:
            run_due_scans()
        except Exception:  # noqa: BLE001 - the scheduler must survive anything
            logger.exception("Delphi Pulse scheduler iteration failed.")


def start() -> None:
    """Start the scheduler thread. Safe to call twice."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_loop, name="delphi-pulse-scheduler", daemon=True
    )
    _thread.start()
    logger.info("Delphi Pulse scheduler started (check every %ss).", CHECK_INTERVAL_SECONDS)


def stop() -> None:
    """Signal the scheduler thread to stop after its current wait."""
    _stop_event.set()
