"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-23
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

from marginalia.db.models import Base  # noqa: F401  (registers all tables)
from marginalia.db.models.ai_structural import INBOX_CATALOG_ID


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    now = datetime.now(timezone.utc).isoformat()
    bind.execute(
        sa.text(
            "INSERT INTO catalogs (id, parent_id, name, summary, description, "
            "extra, tags, is_system, deleted_at, created_at, updated_at) "
            "VALUES (:id, NULL, :name, NULL, NULL, NULL, NULL, :is_system, "
            "NULL, :now, :now)"
        ),
        {
            "id": INBOX_CATALOG_ID,
            "name": "_inbox",
            "is_system": True,
            "now": now,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
