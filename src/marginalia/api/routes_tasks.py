"""Task introspection HTTP routes.

These endpoints expose minimal task-queue state for CLI bookkeeping —
e.g. the embedded REPL checks `running-count` before exit so the user
can choose to wait for in-flight ingest work to finish before the
TaskRunner dies with the process.

These are not the worker's RPC surface; the worker reads the queue
directly from the DB.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marginalia.db.models import Task
from marginalia.db.session import get_session

router = APIRouter(tags=["tasks"])


@router.get("/tasks/running-count")
async def running_count(
    db: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Count tasks currently in `running` or `pending` status.

    Returned counts include both states because in embedded mode,
    pending tasks won't progress once the CLI exits either — the user
    cares about everything still on the queue, not just the in-flight
    rows.
    """
    rows = (
        await db.execute(
            select(Task.status, func.count(Task.id))
            .where(Task.status.in_(("running", "pending")))
            .group_by(Task.status)
        )
    ).all()
    counts = {row[0]: row[1] for row in rows}
    return {
        "running": int(counts.get("running", 0)),
        "pending": int(counts.get("pending", 0)),
    }
