"""relation vetting columns

Revision ID: 0002_relation_vetting
Revises: 0001_initial
Create Date: 2026-05-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002_relation_vetting"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen source_kind: pre-existing column was String(16), too small for
    # 'mine_session_cooccurrence' (24) and 'mine_citation_graph' (20).
    with op.batch_alter_table("entry_relations") as batch:
        batch.alter_column(
            "source_kind",
            existing_type=sa.String(16),
            type_=sa.String(40),
            existing_nullable=False,
        )
        batch.add_column(sa.Column("vetted", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("vetted_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column(
            "vetted_at", sa.DateTime(timezone=True), nullable=True
        ))
        batch.add_column(sa.Column(
            "vetted_observation_count", sa.Integer(), nullable=True
        ))


def downgrade() -> None:
    with op.batch_alter_table("entry_relations") as batch:
        batch.drop_column("vetted_observation_count")
        batch.drop_column("vetted_at")
        batch.drop_column("vetted_reason")
        batch.drop_column("vetted")
        batch.alter_column(
            "source_kind",
            existing_type=sa.String(40),
            type_=sa.String(16),
            existing_nullable=False,
        )
