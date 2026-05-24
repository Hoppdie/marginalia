"""Idempotent schema bootstrap — used by app startup and by 0001_initial.

`bootstrap_schema(bind)` creates every table defined on `Base.metadata` and
seeds the `_inbox` system catalog if absent. Called from:

  - `marginalia.main.lifespan` (FastAPI startup)
  - `marginalia.worker._arun` (worker daemon startup)
  - `alembic/versions/0001_initial.py` (when migrating from empty schema)

Re-runnable: `create_all` is a no-op when tables already exist; the inbox
seed uses `INSERT ... WHERE NOT EXISTS`. Column additions on existing
tables are handled by `_apply_additive_columns()` — we keep a small
hand-written list there because `create_all` only creates whole tables,
never adds missing columns to existing ones.
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa

from marginalia.db.engine import get_engine
from marginalia.db.models import Base  # noqa: F401  (registers all tables)
from marginalia.db.models.ai_structural import INBOX_CATALOG_ID


# Additive columns that landed after the v1 snapshot. Each entry:
# (table, column, ddl-fragment-after-name). Run ALTER TABLE ADD COLUMN
# only when the column is missing — both SQLite and Postgres support that.
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("conversations", "total_cache_read", "INTEGER NOT NULL DEFAULT 0"),
    ("sessions", "total_cache_read", "INTEGER NOT NULL DEFAULT 0"),
)


def _apply_additive_columns(bind) -> None:
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table, column, ddl in _ADDITIVE_COLUMNS:
        if table not in existing_tables:
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        if column in cols:
            continue
        bind.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def bootstrap_schema_sync(bind) -> None:
    """Synchronous variant — runs against a sync connection / engine.

    Used by Alembic migrations (which receive a sync bind from
    `op.get_bind()`) and by `bootstrap_schema()` below via `run_sync`.
    """
    Base.metadata.create_all(bind=bind)
    _apply_additive_columns(bind)
    now = datetime.now(timezone.utc).isoformat()
    bind.execute(
        sa.text(
            "INSERT INTO catalogs (id, parent_id, name, summary, description, "
            "extra, tags, is_system, deleted_at, created_at, updated_at) "
            "SELECT :id, NULL, :name, NULL, NULL, NULL, NULL, :is_system, "
            "NULL, :now, :now "
            "WHERE NOT EXISTS (SELECT 1 FROM catalogs WHERE id = :id)"
        ),
        {
            "id": INBOX_CATALOG_ID,
            "name": "_inbox",
            "is_system": True,
            "now": now,
        },
    )


async def bootstrap_schema() -> None:
    """Run schema creation + inbox seed against the configured async engine."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(bootstrap_schema_sync)
