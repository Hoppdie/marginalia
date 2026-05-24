"""journal cross-session insight columns

Revision ID: 0003_journal_insight
Revises: 0002_relation_vetting
Create Date: 2026-05-24

Adds:
  - journal.superseded_by_id (self-FK, nullable) — used by `insight` rows
    to chain evolution; reflect_turn rows leave it NULL.
  - ix_journal_source_kind index — `kinds=("insight",)` becomes the default
    search_journal filter once Phase D lands.

The schema accommodates `source_kind="insight"` without further migration —
the existing String(16) column already fits.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003_journal_insight"
down_revision = "0002_relation_vetting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("journal") as batch:
        batch.add_column(sa.Column(
            "superseded_by_id",
            sa.String(36),
            sa.ForeignKey("journal.id", ondelete="SET NULL"),
            nullable=True,
        ))
        batch.create_index(
            "ix_journal_source_kind", ["source_kind"], unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("journal") as batch:
        batch.drop_index("ix_journal_source_kind")
        batch.drop_column("superseded_by_id")
