"""
stats.py — Knowledge-base statistics router for Marginalia
Exposes GET /api/stats with document counts, chunk counts, and storage size.

Mount in your FastAPI app:
    from routers.stats import router as stats_router
    app.include_router(stats_router)
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _get_db_path() -> Optional[Path]:
    candidates = [
        os.environ.get("MARGINALIA_DB_PATH"),
        "./marginalia.db",
        "./data/marginalia.db",
        os.path.expanduser("~/.marginalia/marginalia.db"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None


@router.get("", summary="Knowledge-base statistics")
def get_stats():
    """
    Return counts of documents, chunks, and embeddings in the Marginalia
    SQLite database, plus file size on disk. Useful for monitoring growth
    and planning re-indexing schedules.
    """
    db_path = _get_db_path()
    if db_path is None:
        raise HTTPException(
            status_code=503,
            detail="Marginalia database not found. Has the app been initialized?",
        )
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        tables = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        doc_count = chunk_count = embedding_count = 0
        if "documents" in tables:
            doc_count = cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        if "chunks" in tables:
            chunk_count = cur.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        elif "document_chunks" in tables:
            chunk_count = cur.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        if "embeddings" in tables:
            embedding_count = cur.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")

    db_size = db_path.stat().st_size
    return {
        "documents": doc_count,
        "chunks": chunk_count,
        "embeddings": embedding_count,
        "db_size_bytes": db_size,
        "db_size_mb": round(db_size / 1_048_576, 2),
        "avg_bytes_per_document": round(db_size / doc_count) if doc_count else 0,
    }
