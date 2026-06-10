from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from marginalia.db import bootstrap as bootstrap_module
from marginalia.db.bootstrap import bootstrap_schema_sync
from marginalia.db.fts import ENTRY_METADATA_FTS_TABLE
from marginalia.db.models import File, FileEntry
from marginalia.repositories import entries as entries_repo
from marginalia.utils.ids import new_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_entry_metadata_fts_backfills_and_tracks_updates(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fts.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = _now()
    file_id = new_id()
    entry_id = new_id()

    try:
        async with engine.begin() as conn:
            await conn.run_sync(bootstrap_schema_sync)

        async with factory() as session:
            has_fts = (
                await session.execute(
                    text(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = :name"
                    ),
                    {"name": ENTRY_METADATA_FTS_TABLE},
                )
            ).scalar_one_or_none()
            if not has_fts:
                pytest.skip("SQLite build does not provide FTS5 trigram")

            session.add(File(
                id=file_id,
                storage_key="00/aa/fts",
                sha256="a" * 64,
                size_bytes=10,
                mime_type="text/plain",
                original_ext=".txt",
                kind="text",
                summary="Consensus notes mention the raft protocol.",
                description=None,
                extra="replicated log",
                ingest_status="done",
                ingested_at=now,
                deleted_at=None,
                created_at=now,
                updated_at=now,
            ))
            session.add(FileEntry(
                id=entry_id,
                folder_id=None,
                file_id=file_id,
                display_name="paper.txt",
                lifecycle="active",
                catalog_id=None,
                extra="leader election",
                deleted_at=None,
                purge_after=None,
                created_at=now,
                updated_at=now,
            ))
            await session.commit()

        async with factory() as session:
            direct = (
                await session.execute(
                    text(
                        "SELECT entry_id FROM entry_metadata_fts "
                        "WHERE entry_metadata_fts MATCH :query"
                    ),
                    {"query": '"aft"'},
                )
            ).scalars().all()
            assert direct == [entry_id]

            rows = await entries_repo.search_filtered(
                session,
                text=["aft"],
                lifecycle=["active"],
                limit=10,
            )
            total = await entries_repo.count_filtered(
                session,
                text=["aft"],
                lifecycle=["active"],
            )
            assert [entry.id for entry, _file in rows] == [entry_id]
            assert total == 1

            entry = await session.get(FileEntry, entry_id)
            assert entry is not None
            entry.display_name = "paxos-notes.txt"
            entry.extra = "quorum reads"
            file_row = await session.get(File, file_id)
            assert file_row is not None
            file_row.summary = "No old consensus keyword remains here."
            await session.commit()

        async with factory() as session:
            new_match = (
                await session.execute(
                    text(
                        "SELECT entry_id FROM entry_metadata_fts "
                        "WHERE entry_metadata_fts MATCH :query"
                    ),
                    {"query": '"pax"'},
                )
            ).scalars().all()
            old_match = (
                await session.execute(
                    text(
                        "SELECT entry_id FROM entry_metadata_fts "
                        "WHERE entry_metadata_fts MATCH :query"
                    ),
                    {"query": '"aft"'},
                )
            ).scalars().all()
            assert new_match == [entry_id]
            assert old_match == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_entry_metadata_fts_keeps_short_cjk_terms_in_mixed_queries(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cjk.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = _now()

    cjk_file_id = new_id()
    cjk_entry_id = new_id()
    english_file_id = new_id()
    english_entry_id = new_id()

    try:
        async with engine.begin() as conn:
            await conn.run_sync(bootstrap_schema_sync)

        async with factory() as session:
            has_fts = (
                await session.execute(
                    text(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = :name"
                    ),
                    {"name": ENTRY_METADATA_FTS_TABLE},
                )
            ).scalar_one_or_none()
            if not has_fts:
                pytest.skip("SQLite build does not provide FTS5 trigram")

            session.add(File(
                id=cjk_file_id,
                storage_key="00/aa/cjk",
                sha256="d" * 64,
                size_bytes=10,
                mime_type="text/plain",
                original_ext=".txt",
                kind="text",
                summary="数据治理模型说明。",
                description=None,
                extra="",
                ingest_status="done",
                ingested_at=now,
                deleted_at=None,
                created_at=now,
                updated_at=now,
            ))
            session.add(FileEntry(
                id=cjk_entry_id,
                folder_id=None,
                file_id=cjk_file_id,
                display_name="data-governance.txt",
                lifecycle="active",
                catalog_id=None,
                extra="",
                deleted_at=None,
                purge_after=None,
                created_at=now,
                updated_at=now,
            ))
            session.add(File(
                id=english_file_id,
                storage_key="00/aa/english",
                sha256="e" * 64,
                size_bytes=10,
                mime_type="text/plain",
                original_ext=".txt",
                kind="text",
                summary="Transformer architecture notes.",
                description=None,
                extra="",
                ingest_status="done",
                ingested_at=now,
                deleted_at=None,
                created_at=now,
                updated_at=now,
            ))
            session.add(FileEntry(
                id=english_entry_id,
                folder_id=None,
                file_id=english_file_id,
                display_name="transformer.txt",
                lifecycle="active",
                catalog_id=None,
                extra="",
                deleted_at=None,
                purge_after=None,
                created_at=now,
                updated_at=now,
            ))
            await session.commit()

        async with factory() as session:
            rows = await entries_repo.search_filtered(
                session,
                text=["transformer", "数据"],
                lifecycle=["active"],
                limit=10,
            )
            total = await entries_repo.count_filtered(
                session,
                text=["transformer", "数据"],
                lifecycle=["active"],
            )

        ids = {entry.id for entry, _file in rows}
        assert cjk_entry_id in ids
        assert english_entry_id in ids
        assert total == 2
    finally:
        await engine.dispose()


def test_postgres_metadata_fts_query_uses_tsvector_and_cjk_like() -> None:
    search = entries_repo._MetadataTextSearch(  # noqa: SLF001
        dialect="postgresql",
        fts_query='"transformer"',
        like_terms=("数据",),
    )
    stmt = (
        select(FileEntry.id)
        .select_from(FileEntry.__table__.join(File.__table__, File.id == FileEntry.file_id))
    )
    stmt = entries_repo._apply_metadata_fts_filter(stmt, search)  # noqa: SLF001
    stmt = stmt.order_by(entries_repo._metadata_fts_ordering(search))  # noqa: SLF001
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "to_tsvector" in sql
    assert "websearch_to_tsquery" in sql
    assert "@@" in sql
    assert "ILIKE" in sql
    assert "ts_rank_cd" in sql
    assert "coalesce_" not in sql


def test_postgres_metadata_fts_bootstrap_creates_expression_gin_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, stmt) -> None:  # noqa: ANN001
            self.sql.append(str(stmt))

    class _Inspector:
        def get_table_names(self) -> list[str]:
            return ["files", "file_entries"]

    bind = _Bind()
    monkeypatch.setattr(bootstrap_module.sa, "inspect", lambda _bind: _Inspector())

    bootstrap_module._ensure_postgres_metadata_fts_indexes(bind)  # noqa: SLF001

    joined = "\n".join(bind.sql)
    assert "ix_file_entries_metadata_fts_pg" in joined
    assert "ix_files_metadata_fts_pg" in joined
    assert "USING gin" in joined
    assert "to_tsvector" in joined


@pytest.mark.asyncio
async def test_entry_metadata_fts_searches_description_and_ranks_by_match_quality(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rank.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = _now()

    relevant_file_id = new_id()
    relevant_entry_id = new_id()
    noise_file_id = new_id()
    noise_entry_id = new_id()

    try:
        async with engine.begin() as conn:
            await conn.run_sync(bootstrap_schema_sync)

        async with factory() as session:
            has_fts = (
                await session.execute(
                    text(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = :name"
                    ),
                    {"name": ENTRY_METADATA_FTS_TABLE},
                )
            ).scalar_one_or_none()
            if not has_fts:
                pytest.skip("SQLite build does not provide FTS5 trigram")

            session.add(File(
                id=relevant_file_id,
                storage_key="00/aa/relevant",
                sha256="b" * 64,
                size_bytes=10,
                mime_type="text/plain",
                original_ext=".txt",
                kind="text",
                summary="Protocol notes.",
                description={
                    "sections": [
                        {
                            "title": "Consensus",
                            "summary": "Raft uses leader election.",
                            "key_terms": ["raft", "leader election"],
                        }
                    ]
                },
                extra="",
                ingest_status="done",
                ingested_at=now,
                deleted_at=None,
                created_at=now,
                updated_at=now,
            ))
            session.add(FileEntry(
                id=relevant_entry_id,
                folder_id=None,
                file_id=relevant_file_id,
                display_name="older-relevant.txt",
                lifecycle="active",
                catalog_id=None,
                extra="",
                deleted_at=None,
                purge_after=None,
                created_at=now,
                updated_at=now,
            ))
            session.add(File(
                id=noise_file_id,
                storage_key="00/aa/noise",
                sha256="c" * 64,
                size_bytes=10,
                mime_type="text/plain",
                original_ext=".txt",
                kind="text",
                summary="Raft background notes.",
                description=None,
                extra="",
                ingest_status="done",
                ingested_at=now + timedelta(seconds=1),
                deleted_at=None,
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            ))
            session.add(FileEntry(
                id=noise_entry_id,
                folder_id=None,
                file_id=noise_file_id,
                display_name="newer-noise.txt",
                lifecycle="active",
                catalog_id=None,
                extra="",
                deleted_at=None,
                purge_after=None,
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            ))
            await session.commit()

        async with factory() as session:
            rows = await entries_repo.search_filtered(
                session,
                text=["raft", "leader."],
                lifecycle=["active"],
                limit=10,
            )

        assert [entry.id for entry, _file in rows] == [
            relevant_entry_id,
            noise_entry_id,
        ]
    finally:
        await engine.dispose()
