from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from marginalia.config import get_settings
from marginalia.db.engine import dispose_engine, get_engine
from marginalia.db.models import Base, Task
from marginalia.db.models.task_outcomes import TaskOutcome
from marginalia.db.session import session_scope
from marginalia.repositories.task_outcomes import GLOBAL_OBJECT_ID, GLOBAL_OBJECT_KIND, record_outcome
from marginalia.tasks.handlers.periodic_tick import handle_periodic_tick
from marginalia.tasks.kinds import (
    KIND_INGEST_FILE,
    KIND_MINE_RELATIONS,
    KIND_PERIODIC_TICK,
    KIND_REFLECT_TURN,
    KIND_TAG_QUALITY,
)
from marginalia.tasks.maintenance_budget import (
    LOW_PRIORITY_MAINTENANCE_KINDS,
    MAINTENANCE_BUDGET_SKIP_REASON,
    read_maintenance_budget,
)


async def _prepare_home(monkeypatch: pytest.MonkeyPatch, tmp_path, *, budget: int) -> None:
    monkeypatch.setenv("MARGINALIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("WORKER_ENABLED", "false")
    monkeypatch.setenv("AUTO_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("RELATION_BACKGROUND_VETTING_ENABLED", "true")
    monkeypatch.setenv("LLM_DEFAULT_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "fake-model")
    monkeypatch.setenv("MAINTENANCE_DAILY_TOKEN_BUDGET", str(budget))
    get_settings.cache_clear()  # type: ignore[attr-defined]
    await dispose_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_periodic_low_priority_skips_when_maintenance_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    await _prepare_home(monkeypatch, tmp_path, budget=100)
    now = datetime.now(timezone.utc)
    async with session_scope() as db:
        await record_outcome(
            db,
            task_kind=KIND_TAG_QUALITY,
            object_kind="task",
            object_id="tag-quality-run",
            outcome="applied",
            detail={"tokens_in": 80, "tokens_out": 30},
            completed_at=now - timedelta(hours=1),
        )
        await record_outcome(
            db,
            task_kind=KIND_INGEST_FILE,
            object_kind="task",
            object_id="ingest-run",
            outcome="applied",
            detail={"tokens_in": 10_000, "tokens_out": 2_000},
            completed_at=now - timedelta(hours=1),
        )
        await db.commit()

    async with session_scope() as db:
        state = await read_maintenance_budget(db, settings=get_settings(), now=now)
    assert state.used == 110
    assert state.exhausted is True

    await handle_periodic_tick({})

    async with session_scope() as db:
        task_kinds = set(
            (
                await db.execute(
                    select(Task.kind).where(Task.kind != KIND_PERIODIC_TICK)
                )
            ).scalars().all()
        )
        assert not (task_kinds & LOW_PRIORITY_MAINTENANCE_KINDS)
        assert KIND_TAG_QUALITY in task_kinds
        assert KIND_MINE_RELATIONS in task_kinds

        deferred = (
            await db.execute(
                select(TaskOutcome.task_kind, TaskOutcome.detail).where(
                    TaskOutcome.object_kind == GLOBAL_OBJECT_KIND,
                    TaskOutcome.object_id == GLOBAL_OBJECT_ID,
                    TaskOutcome.outcome == "deferred",
                )
            )
        ).all()
        assert {kind for kind, _detail in deferred} == LOW_PRIORITY_MAINTENANCE_KINDS
        for _kind, detail in deferred:
            assert detail["reason"] == MAINTENANCE_BUDGET_SKIP_REASON
            assert detail["maintenance_daily_token_budget"] == 100
            assert detail["maintenance_tokens_used"] == 110

        tick_detail = (
            await db.execute(
                select(TaskOutcome.detail)
                .where(TaskOutcome.task_kind == KIND_PERIODIC_TICK)
                .order_by(TaskOutcome.completed_at.desc())
            )
        ).scalars().first()
        assert tick_detail is not None
        assert {
            item["kind"] for item in tick_detail["skipped_budget"]
        } == LOW_PRIORITY_MAINTENANCE_KINDS


@pytest.mark.asyncio
async def test_core_ingest_and_reflect_usage_do_not_exhaust_maintenance_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    await _prepare_home(monkeypatch, tmp_path, budget=100)
    now = datetime.now(timezone.utc)
    async with session_scope() as db:
        for kind in (KIND_INGEST_FILE, KIND_REFLECT_TURN):
            await record_outcome(
                db,
                task_kind=kind,
                object_kind="task",
                object_id=f"{kind}-run",
                outcome="applied",
                detail={"tokens_in": 10_000, "tokens_out": 2_000},
                completed_at=now - timedelta(hours=1),
            )
        await db.commit()

    async with session_scope() as db:
        state = await read_maintenance_budget(db, settings=get_settings(), now=now)
    assert state.used == 0
    assert state.exhausted is False

    await handle_periodic_tick({})

    async with session_scope() as db:
        task_kinds = set(
            (
                await db.execute(
                    select(Task.kind).where(Task.kind != KIND_PERIODIC_TICK)
                )
            ).scalars().all()
        )
        assert LOW_PRIORITY_MAINTENANCE_KINDS <= task_kinds
        deferred = (
            await db.execute(
                select(TaskOutcome.id).where(TaskOutcome.outcome == "deferred")
            )
        ).scalars().all()
        assert deferred == []
