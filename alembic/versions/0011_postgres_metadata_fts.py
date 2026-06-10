"""add postgres metadata fts indexes

Revision ID: 0011_postgres_metadata_fts
Revises: 0010_entry_metadata_fts_description
Create Date: 2026-06-10

Adds expression GIN indexes for the Postgres metadata search path. SQLite
continues to use the entry_metadata_fts FTS5 virtual table.
"""
from __future__ import annotations

from alembic import op

from marginalia.db.bootstrap import (
    _drop_postgres_metadata_fts_indexes,
    _ensure_postgres_metadata_fts_indexes,
)


revision = "0011_postgres_metadata_fts"
down_revision = "0010_entry_metadata_fts_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _ensure_postgres_metadata_fts_indexes(op.get_bind())


def downgrade() -> None:
    _drop_postgres_metadata_fts_indexes(op.get_bind())
