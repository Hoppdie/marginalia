from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

_TEST_PARENT = Path(os.environ.get("MARGINALIA_TEST_TMP", Path(__file__).resolve().parent))
_TEST_ROOT = _TEST_PARENT / f"_reflect_turn_invalidation_e2e_data_{os.getpid()}_{uuid4().hex[:8]}"
_TEST_ROOT.mkdir(parents=True)
os.environ["MARGINALIA_HOME"] = str(_TEST_ROOT)
os.environ["STORAGE_BACKEND"] = "local"
os.environ["WORKER_ENABLED"] = "false"
os.environ["LLM_DEFAULT_API_KEY"] = "sk-fake"
os.environ["LLM_DEFAULT_MODEL"] = "fake-model"

from marginalia.agent.tools.search_journal import run_search_journal
from marginalia.config import get_settings
from marginalia.db.bootstrap import bootstrap_schema_sync
from marginalia.db.engine import get_engine, get_session_factory
from marginalia.db.models import (
    Conversation,
    File,
    FileEntry,
    Journal,
    Session,
    TaskOutcome,
)
from marginalia.llm.types import ChatRequest, ChatResponse, TokenUsage
from marginalia.tasks.handlers.reflect_turn import handle_reflect_turn
from marginalia.utils.ids import new_id

get_settings.cache_clear()  # type: ignore[attr-defined]


REFLECT_REQUESTS: list[ChatRequest] = []


class _FakeReflectClient:
    profile_name = "reflect"
    model = "fake-reflect"

    def __init__(self, *, old_journal_id: str, entry_id: str) -> None:
        self.old_journal_id = old_journal_id
        self.entry_id = entry_id

    async def complete(self, request: ChatRequest) -> ChatResponse:
        REFLECT_REQUESTS.append(request)
        text = (
            "<entry>\n"
            "question: What is the policy?\n"
            "answer: Policy B is now correct.\n"
            f"entry_ids: {self.entry_id}\n"
            "tags: policy\n"
            "</entry>\n\n"
            "<invalidates>\n"
            f"{self.old_journal_id}: Policy A is contradicted by the new answer.\n"
            "</invalidates>"
        )
        return ChatResponse(
            text=text,
            tool_calls=[],
            stop_reason="end_turn",
            usage=TokenUsage(input_tokens=1200, output_tokens=120),
            parsed_json=None,
        )


def _install_reflect_client(client: _FakeReflectClient) -> None:
    import marginalia.tasks.handlers.reflect_turn as module

    module.get_chat_client = lambda _profile="reflect": client  # type: ignore[assignment]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _create_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(bootstrap_schema_sync)


async def _seed() -> dict[str, str]:
    factory = get_session_factory()
    now = _now()
    entry_id = new_id()
    file_id = new_id()
    old_session_id = new_id()
    old_conv_id = new_id()
    old_journal_id = new_id()
    current_session_id = new_id()
    current_conv_id = new_id()

    async with factory() as session:
        session.add(File(
            id=file_id,
            storage_key="policy.txt",
            sha256="a" * 64,
            size_bytes=16,
            mime_type="text/plain",
            original_ext=".txt",
            kind="text",
            summary="policy",
            description=None,
            extra=None,
            ingest_status="done",
            ingested_at=now - timedelta(days=5),
            deleted_at=None,
            created_at=now - timedelta(days=5),
            updated_at=now - timedelta(days=5),
        ))
        await session.flush()
        session.add(FileEntry(
            id=entry_id,
            folder_id=None,
            file_id=file_id,
            display_name="policy.txt",
            lifecycle="active",
            catalog_id=None,
            extra=None,
            deleted_at=None,
            purge_after=None,
            created_at=now - timedelta(days=5),
            updated_at=now - timedelta(days=5),
        ))
        session.add(Session(
            id=old_session_id,
            started_at=now - timedelta(days=4),
            ended_at=now - timedelta(days=4),
            end_reason="normal",
            initiating_user_message="old",
            turn_count=1,
            total_input_tokens=0,
            total_output_tokens=0,
            total_cache_read=0,
            total_tool_calls=0,
            total_llm_calls=0,
            total_duration_ms=0,
        ))
        await session.flush()
        session.add(Conversation(
            id=old_conv_id,
            session_id=old_session_id,
            turn_index=0,
            started_at=now - timedelta(days=4),
            ended_at=now - timedelta(days=4),
            user_message="old policy?",
            agent_response="Policy A is correct.",
            tool_calls=[],
            llm_calls=[],
            total_input_tokens=0,
            total_output_tokens=0,
            total_tool_calls=0,
            total_llm_calls=0,
            total_duration_ms=0,
        ))
        await session.flush()
        session.add(Journal(
            id=old_journal_id,
            conversation_id=old_conv_id,
            note="Q: What is the policy?\nA: Policy A is correct.",
            entry_ids=[entry_id],
            tags=["policy"],
            source_kind="reflect_turn",
            created_at=now - timedelta(days=4),
        ))
        await session.flush()
        session.add(Session(
            id=current_session_id,
            started_at=now - timedelta(hours=1),
            ended_at=now,
            end_reason="normal",
            initiating_user_message="current",
            turn_count=1,
            total_input_tokens=0,
            total_output_tokens=0,
            total_cache_read=0,
            total_tool_calls=0,
            total_llm_calls=0,
            total_duration_ms=0,
        ))
        await session.flush()
        session.add(Conversation(
            id=current_conv_id,
            session_id=current_session_id,
            turn_index=0,
            started_at=now - timedelta(minutes=10),
            ended_at=now - timedelta(minutes=9),
            user_message="what is the policy now?",
            agent_response="Policy B is now correct.",
            tool_calls=[{
                "name": "read_files",
                "arguments": {"entry_id": entry_id},
                "result": {"entry_id": entry_id, "text": "Policy B"},
            }],
            llm_calls=[],
            total_input_tokens=0,
            total_output_tokens=0,
            total_tool_calls=1,
            total_llm_calls=1,
            total_duration_ms=10,
        ))
        await session.commit()

    return {
        "entry_id": entry_id,
        "old_journal_id": old_journal_id,
        "current_conv_id": current_conv_id,
    }


@pytest.mark.asyncio
async def test_reflect_turn_invalidates_contradicted_prior_journal() -> None:
    seeded = await _seed()
    _install_reflect_client(_FakeReflectClient(
        old_journal_id=seeded["old_journal_id"],
        entry_id=seeded["entry_id"],
    ))

    await handle_reflect_turn({"conversation_id": seeded["current_conv_id"]})

    factory = get_session_factory()
    async with factory() as session:
        old = await session.get(Journal, seeded["old_journal_id"])
        assert old is not None
        assert old.invalidated_at is not None
        assert old.invalidated_by_id is not None
        assert "Policy A" in (old.invalidated_reason or "")

        new_note = (
            await session.execute(
                select(Journal).where(Journal.id == old.invalidated_by_id)
            )
        ).scalar_one()
        assert "Policy B is now correct" in new_note.note
        assert new_note.invalidated_at is None

        active = await run_search_journal(
            session,
            {"text": "Policy", "limit": 10, "since_days": 10},
        )
        assert [note["id"] for note in active["notes"]] == [new_note.id]

        audit = await run_search_journal(
            session,
            {
                "text": "Policy",
                "limit": 10,
                "since_days": 10,
                "include_invalidated": True,
            },
        )
        audit_by_id = {note["id"]: note for note in audit["notes"]}
        assert seeded["old_journal_id"] in audit_by_id
        assert audit_by_id[seeded["old_journal_id"]]["invalidated_by_id"] == new_note.id

        outcome = (
            await session.execute(
                select(TaskOutcome).where(
                    TaskOutcome.task_kind == "reflect_turn",
                    TaskOutcome.object_id == seeded["current_conv_id"],
                )
            )
        ).scalar_one()
        assert outcome.detail["journal_entries"] == 1
        assert outcome.detail["invalidated_journal_entries"] == 1

    assert REFLECT_REQUESTS
    tail = str(REFLECT_REQUESTS[0].messages[-1].content)
    assert seeded["old_journal_id"] in tail
    assert "Prior active journal notes" in tail


async def main() -> None:
    await _create_schema()
    await test_reflect_turn_invalidates_contradicted_prior_journal()
    print("\nALL REFLECT INVALIDATION CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
