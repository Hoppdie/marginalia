"""files.kind: extend CHECK for supplemental kinds

Revision ID: 0014_files_kind_check
Revises: 0013_reconcile_dead_ingest_files
Create Date: 2026-06-25

Adds `email` and `ebook` to the legal files.kind values used by
supplemental extraction pipelines.
"""
from __future__ import annotations

from alembic import op

from marginalia.db.bootstrap import _relax_files_kind_check


revision = "0014_files_kind_check"
down_revision = "0013_reconcile_dead_ingest_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _relax_files_kind_check(op.get_bind())


def downgrade() -> None:
    pass
