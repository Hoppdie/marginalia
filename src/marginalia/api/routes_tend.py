"""User-triggered maintenance pass — design.md §9 / §16.1.

`POST /v1/tend` enqueues a one-shot run of the librarian's maintenance
chain (normalize_tags → enrich_tags → restructure_catalogs →
mine_corpus_evidence → mine_session_cooccurrence → propose_views →
refresh_entry_extra). Returns immediately with a run_id and the list of
task ids that will execute. Progress is queried via GET /v1/tend/{id}.

Why this exists: most users don't want to wait days for the periodic
dispatcher to run normalize_tags every 6 hours. After bulk-ingesting a
batch of files, calling /tend once forces a tidy-up pass right now.

Dedup: if a periodic equivalent of any of these tasks is already
pending/running, the existing row is reused (so /tend doesn't pile up
duplicate work). Each kind's resulting task id is persisted in a
`task_outcomes` row of kind=tend_dispatch so progress lookups are O(1).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marginalia.db.models import Task
from marginalia.db.models.task_outcomes import TaskOutcome
from marginalia.db.session import get_session
from marginalia.services.audit import write_event
from marginalia.services.task_outcomes import record_outcome
from marginalia.tasks.enqueue import enqueue
from marginalia.tasks.kinds import (
    KIND_ENRICH_TAGS,
    KIND_MINE_CORPUS_EVIDENCE,
    KIND_MINE_SESSION_COOCCURRENCE,
    KIND_NORMALIZE_TAGS,
    KIND_PROPOSE_VIEWS,
    KIND_REFRESH_ENTRY_EXTRA,
    KIND_RESTRUCTURE_CATALOGS,
)
from marginalia.utils.ids import new_id

log = logging.getLogger(__name__)

router = APIRouter(tags=["tend"])

# Order is the priority chain from kinds.py. normalize first (tags must be
# clean before enrich); restructure after enrich (catalogs need stable tags);
# mining and propose_views run after structural settling; refresh_entry_extra
# closes out using everything that came before.
TEND_CHAIN: tuple[str, ...] = (
    KIND_NORMALIZE_TAGS,
    KIND_ENRICH_TAGS,
    KIND_RESTRUCTURE_CATALOGS,
    KIND_MINE_CORPUS_EVIDENCE,
    KIND_MINE_SESSION_COOCCURRENCE,
    KIND_PROPOSE_VIEWS,
    KIND_REFRESH_ENTRY_EXTRA,
)

TEND_OBJECT_KIND = "tend_run"
TEND_DISPATCH_KIND = "tend_dispatch"


@router.post("/tend", status_code=202)
async def post_tend(
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Kick off a maintenance pass. Returns the run_id and per-kind task ids."""
    run_id = new_id()
    dispatched: list[dict[str, Any]] = []
    for kind in TEND_CHAIN:
        existing = (
            await db.execute(
                select(Task).where(
                    Task.dedup_key == kind,
                    Task.status.in_(("pending", "running")),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            dispatched.append({
                "kind": kind,
                "task_id": existing.id,
                "skipped": True,
                "status": existing.status,
            })
            continue
        task = await enqueue(
            db,
            kind=kind,
            payload={"tend_run_id": run_id},
            dedup_key=kind,
        )
        if task is None:
            # enqueue returns None only when dedup+race lost — should not
            # happen here since we already queried, but stay robust.
            dispatched.append(
                {"kind": kind, "task_id": None, "skipped": True}
            )
            continue
        dispatched.append({
            "kind": kind,
            "task_id": task.id,
            "skipped": False,
            "status": task.status,
        })
        await write_event(
            db,
            kind="task_enqueued",
            task_id=task.id,
            payload={"kind": kind, "scheduled_by": "tend", "tend_run_id": run_id},
        )

    await record_outcome(
        db,
        task_kind=TEND_DISPATCH_KIND,
        object_kind=TEND_OBJECT_KIND,
        object_id=run_id,
        outcome="applied",
        detail={"chain": list(TEND_CHAIN), "dispatched": dispatched},
        task_run_id=run_id,
    )
    await db.commit()

    return {
        "tend_run_id": run_id,
        "tasks": dispatched,
    }


@router.get("/tend/{run_id}")
async def get_tend(
    run_id: str,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Look up a tend run's current state.

    Reads the dispatch row written at /tend time (which lists the task ids),
    then joins live `tasks` rows to report status/started_at/finished_at and
    any outcomes recorded with task_run_id=run_id.
    """
    dispatch = (
        await db.execute(
            select(TaskOutcome).where(
                TaskOutcome.task_kind == TEND_DISPATCH_KIND,
                TaskOutcome.object_kind == TEND_OBJECT_KIND,
                TaskOutcome.object_id == run_id,
            )
        )
    ).scalar_one_or_none()
    if dispatch is None:
        raise HTTPException(status_code=404, detail="tend run not found")

    detail = dispatch.detail or {}
    dispatched = detail.get("dispatched") or []

    task_ids = [d.get("task_id") for d in dispatched if d.get("task_id")]
    tasks_by_id: dict[str, Task] = {}
    if task_ids:
        rows = (
            await db.execute(
                select(Task).where(Task.id.in_(task_ids))
            )
        ).scalars().all()
        tasks_by_id = {t.id: t for t in rows}

    progress: list[dict[str, Any]] = []
    state_counts = {"pending": 0, "running": 0, "done": 0, "error": 0,
                    "skipped": 0, "missing": 0}
    for d in dispatched:
        kind = d.get("kind")
        tid = d.get("task_id")
        if d.get("skipped"):
            state_counts["skipped"] += 1
            progress.append({
                "kind": kind, "task_id": None, "status": "skipped"
            })
            continue
        t = tasks_by_id.get(tid) if tid else None
        if t is None:
            # Task was pruned or never inserted; report as missing.
            state_counts["missing"] += 1
            progress.append({
                "kind": kind, "task_id": tid, "status": "missing"
            })
            continue
        status = t.status
        bucket = status if status in state_counts else "pending"
        state_counts[bucket] += 1
        progress.append({
            "kind": kind,
            "task_id": tid,
            "status": status,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
            "attempts": t.attempts,
            "last_error": t.last_error,
        })

    total = len(dispatched)
    settled = (
        state_counts["done"] + state_counts["error"]
        + state_counts["skipped"] + state_counts["missing"]
    )
    return {
        "tend_run_id": run_id,
        "started_at": dispatch.completed_at.isoformat()
            if dispatch.completed_at else None,
        "total": total,
        "settled": settled,
        "all_settled": settled == total,
        "state_counts": state_counts,
        "progress": progress,
    }
