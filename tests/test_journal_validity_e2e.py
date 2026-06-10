from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from marginalia.agent.tools.search_journal import run_search_journal
from marginalia.db.bootstrap import bootstrap_schema_sync
from marginalia.db.models import Conversation, File, FileEntry, Journal, Session
from marginalia.utils.ids import new_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_search_journal_marks_and_downgrades_stale_entry_references(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'journal.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = _now()

    current_file_id = new_id()
    current_entry_id = new_id()
    reingested_file_id = new_id()
    reingested_entry_id = new_id()
    deleted_file_id = new_id()
    deleted_entry_id = new_id()
    conv_id = new_id()

    try:
        async with engine.begin() as conn:
            await conn.run_sync(bootstrap_schema_sync)

        async with factory() as session:
            session_id = new_id()
            session.add(Session(
                id=session_id,
                started_at=now - timedelta(days=4),
                ended_at=now - timedelta(days=4),
                end_reason="normal",
                initiating_user_message="seed",
                turn_count=1,
                total_input_tokens=0,
                total_output_tokens=0,
                total_cache_read=0,
                total_tool_calls=0,
                total_llm_calls=0,
                total_duration_ms=0,
            ))
            session.add(Conversation(
                id=conv_id,
                session_id=session_id,
                turn_index=0,
                started_at=now - timedelta(days=4),
                ended_at=now - timedelta(days=4),
                user_message="seed",
                agent_response="seed",
                tool_calls=[],
                llm_calls=[],
                total_input_tokens=0,
                total_output_tokens=0,
                total_tool_calls=0,
                total_llm_calls=0,
                total_duration_ms=0,
            ))
            session.add_all([
                File(
                    id=current_file_id,
                    storage_key="aa/current",
                    sha256="a" * 64,
                    size_bytes=10,
                    mime_type="text/plain",
                    original_ext=".txt",
                    kind="text",
                    summary="current",
                    description=None,
                    extra=None,
                    ingest_status="done",
                    ingested_at=now - timedelta(days=4),
                    deleted_at=None,
                    created_at=now - timedelta(days=4),
                    updated_at=now - timedelta(days=4),
                ),
                File(
                    id=reingested_file_id,
                    storage_key="aa/reingested",
                    sha256="b" * 64,
                    size_bytes=10,
                    mime_type="text/plain",
                    original_ext=".txt",
                    kind="text",
                    summary="reingested",
                    description=None,
                    extra=None,
                    ingest_status="done",
                    ingested_at=now,
                    deleted_at=None,
                    created_at=now - timedelta(days=4),
                    updated_at=now,
                ),
                File(
                    id=deleted_file_id,
                    storage_key="aa/deleted",
                    sha256="c" * 64,
                    size_bytes=10,
                    mime_type="text/plain",
                    original_ext=".txt",
                    kind="text",
                    summary="deleted",
                    description=None,
                    extra=None,
                    ingest_status="done",
                    ingested_at=now - timedelta(days=4),
                    deleted_at=None,
                    created_at=now - timedelta(days=4),
                    updated_at=now - timedelta(days=4),
                ),
            ])
            session.add_all([
                FileEntry(
                    id=current_entry_id,
                    folder_id=None,
                    file_id=current_file_id,
                    display_name="current.txt",
                    lifecycle="active",
                    catalog_id=None,
                    extra=None,
                    deleted_at=None,
                    purge_after=None,
                    created_at=now - timedelta(days=4),
                    updated_at=now - timedelta(days=4),
                ),
                FileEntry(
                    id=reingested_entry_id,
                    folder_id=None,
                    file_id=reingested_file_id,
                    display_name="reingested.txt",
                    lifecycle="active",
                    catalog_id=None,
                    extra=None,
                    deleted_at=None,
                    purge_after=None,
                    created_at=now - timedelta(days=4),
                    updated_at=now,
                ),
                FileEntry(
                    id=deleted_entry_id,
                    folder_id=None,
                    file_id=deleted_file_id,
                    display_name="deleted.txt",
                    lifecycle="active",
                    catalog_id=None,
                    extra=None,
                    deleted_at=now,
                    purge_after=now + timedelta(days=30),
                    created_at=now - timedelta(days=4),
                    updated_at=now,
                ),
            ])
            session.add_all([
                Journal(
                    id=new_id(),
                    conversation_id=conv_id,
                    note="consensus stale newer note",
                    entry_ids=[reingested_entry_id, deleted_entry_id],
                    tags=["consensus"],
                    source_kind="insight",
                    created_at=now - timedelta(days=1),
                ),
                Journal(
                    id=new_id(),
                    conversation_id=conv_id,
                    note="consensus current older note",
                    entry_ids=[current_entry_id],
                    tags=["consensus"],
                    source_kind="insight",
                    created_at=now - timedelta(days=2),
                ),
            ])
            await session.commit()

        async with factory() as session:
            result = await run_search_journal(
                session,
                {"text": "consensus", "limit": 10, "since_days": 10},
            )

        notes = result["notes"]
        assert [note["note"] for note in notes] == [
            "consensus current older note",
            "consensus stale newer note",
        ]
        assert notes[0]["entry_validity"]["status"] == "current"
        assert notes[1]["entry_validity"]["status"] == "stale"
        assert notes[1]["validity_note"] == "引用实体已变更"
        stale_entries = {
            item["entry_id"]: item["reason"]
            for item in notes[1]["entry_validity"]["entries"]
        }
        assert stale_entries == {
            reingested_entry_id: "file_reingested_after_note",
            deleted_entry_id: "entry_deleted",
        }
    finally:
        await engine.dispose()
