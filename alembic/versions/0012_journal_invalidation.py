"""add journal contradiction invalidation columns

Revision ID: 0012_journal_invalidation
Revises: 0011_postgres_metadata_fts
Create Date: 2026-06-10

Adds nullable invalidation metadata to journal rows. The helper is
idempotent so this revision is safe on databases where startup bootstrap
has already applied the same additive columns and index.
"""
from __future__ import annotations

from alembic import op

from marginalia.db.bootstrap import _ensure_journal_invalidation


revision = "0012_journal_invalidation"
down_revision = "0011_postgres_metadata_fts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _ensure_journal_invalidation(op.get_bind())


def downgrade() -> None:
    # Keep audit history by default; dropping these columns would discard
    # why older notes were hidden from active recall.
    pass
