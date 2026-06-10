"""Transcript replay must rewrite raw `entry_id=<uuid>` footnotes.

The persisted `agent_response` keeps the raw form on purpose (exports
parse it). For the chat sidebar reload, GET /v1/sessions/{id}/messages
should return the human-readable form: `[name](entry:<uuid>)`.

Run:
    .venv/Scripts/python -m pytest tests/test_session_messages_e2e.py -x -q
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_TEST_PARENT = Path(os.environ.get("MARGINALIA_TEST_TMP", Path(__file__).resolve().parent))
_TEST_ROOT = _TEST_PARENT / f"_session_messages_e2e_data_{os.getpid()}_{uuid4().hex[:8]}"
_TEST_ROOT.mkdir(parents=True)
os.environ["MARGINALIA_HOME"] = str(_TEST_ROOT)
os.environ["STORAGE_BACKEND"] = "local"
os.environ["WORKER_ENABLED"] = "false"
os.environ["LLM_DEFAULT_API_KEY"] = "sk-fake"
os.environ["LLM_DEFAULT_MODEL"] = "fake-model"

import httpx
from httpx import ASGITransport

from marginalia.config import get_settings
get_settings.cache_clear()  # type: ignore[attr-defined]

from marginalia.db.engine import get_engine, get_session_factory
from marginalia.db.models import (
    Base, Conversation, File, FileEntry, Folder, Session,
)
from marginalia.main import app
from marginalia.storage import get_storage
from marginalia.utils.ids import new_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _stream_bytes(body: bytes):
    yield body


async def _create_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed() -> dict:
    factory = get_session_factory()
    now = _now()
    async with factory() as s:
        folder = Folder(id=new_id(), parent_id=None, name="papers",
                        created_at=now, updated_at=now)
        s.add(folder); await s.flush()
        f = File(id=new_id(), storage_key=new_id(), sha256="d"*64,
                 size_bytes=10, mime_type="text/plain",
                 original_ext=".md", kind="text",
                 summary="Raft note", description={"sections": []},
                 extra=None, ingest_status="done", ingested_at=now,
                 created_at=now, updated_at=now)
        s.add(f); await s.flush()
        entry = FileEntry(id=new_id(), folder_id=folder.id, file_id=f.id,
                          display_name="raft.md", lifecycle="active",
                          catalog_id=None, extra=None,
                          created_at=now, updated_at=now)
        s.add(entry); await s.flush()

        sess = Session(
            id=new_id(), started_at=now, ended_at=None, end_reason=None,
            initiating_user_message="raft?", turn_count=1,
            total_input_tokens=0, total_output_tokens=0, total_cache_read=0,
            total_tool_calls=0, total_llm_calls=0, total_duration_ms=0,
        )
        s.add(sess); await s.flush()

        agent_response = (
            "Raft 用 leader election 来达成一致[^a]。\n\n"
            f"[^a]: entry_id={entry.id}, section_id=s2 - 这一段写的就是选举\n"
        )
        conv = Conversation(
            id=new_id(), session_id=sess.id, turn_index=0,
            started_at=now, ended_at=_now(),
            user_message="raft?", agent_response=agent_response,
            tool_calls=[],
            llm_calls=[{
                "phase": "plan",
                "model": "fake",
                "plan_text": "1. Read the Raft note.\nSession name: Raft note",
            }],
            total_input_tokens=0, total_output_tokens=0,
            total_tool_calls=0, total_llm_calls=0, total_duration_ms=0,
        )
        s.add(conv)
        await s.commit()
        return {"sid": sess.id, "eid": entry.id, "raw": agent_response}


async def _seed_quote_status() -> dict:
    factory = get_session_factory()
    storage = get_storage()
    now = _now()
    body = (
        "This note says leader-election is the central Raft mechanism.\n"
        "A second line mentions replicated logs for context.\n"
    ).encode("utf-8")
    storage_key = f"{new_id()}.txt"
    await storage.put(storage_key, _stream_bytes(body), content_type="text/plain")

    async with factory() as s:
        folder = Folder(id=new_id(), parent_id=None, name="quotes",
                        created_at=now, updated_at=now)
        s.add(folder); await s.flush()
        f = File(id=new_id(), storage_key=storage_key, sha256="e"*64,
                 size_bytes=len(body), mime_type="text/plain",
                 original_ext=".txt", kind="text",
                 summary="Quote note", description={"sections": []},
                 extra=None, ingest_status="done", ingested_at=now,
                 created_at=now, updated_at=now)
        s.add(f); await s.flush()
        entry = FileEntry(id=new_id(), folder_id=folder.id, file_id=f.id,
                          display_name="quotes.txt", lifecycle="active",
                          catalog_id=None, extra=None,
                          created_at=now, updated_at=now)
        s.add(entry); await s.flush()

        sess = Session(
            id=new_id(), started_at=now, ended_at=None, end_reason=None,
            initiating_user_message="quotes?", turn_count=1,
            total_input_tokens=0, total_output_tokens=0, total_cache_read=0,
            total_tool_calls=0, total_llm_calls=0, total_duration_ms=0,
        )
        s.add(sess); await s.flush()

        agent_response = (
            "真实引用会被验证[^v]，编造引用仍会保留但标记[^u]。\n\n"
            f'[^v]: entry_id={entry.id}, quote="leader election" - 连字符差异应被容忍\n'
            f'[^u]: entry_id={entry.id}, quote="invented copied sentence" - 这句不在原文\n'
        )
        conv = Conversation(
            id=new_id(), session_id=sess.id, turn_index=0,
            started_at=now, ended_at=_now(),
            user_message="quotes?", agent_response=agent_response,
            tool_calls=[], llm_calls=[],
            total_input_tokens=0, total_output_tokens=0,
            total_tool_calls=0, total_llm_calls=0, total_duration_ms=0,
        )
        s.add(conv)
        await s.commit()
        return {"sid": sess.id, "eid": entry.id}


async def test_transcript_rewrites_entry_id() -> None:
    seeded = await _seed()
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get(f"/v1/sessions/{seeded['sid']}/messages")
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body["turns"]) == 1
            ar = body["turns"][0]["agent_response"]
            assert f"[raft.md](entry:{seeded['eid']})" in ar, ar
            assert "entry_id=" not in ar, ar
            plan = body["turns"][0]["plan_text"]
            assert plan == "Read the Raft note.", plan
            print("[1] transcript rewrites raw entry_id to [name](entry:<uuid>)")


async def test_transcript_marks_quote_verification_status() -> None:
    seeded = await _seed_quote_status()
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get(f"/v1/sessions/{seeded['sid']}/messages")
            assert r.status_code == 200, r.text
            ar = r.json()["turns"][0]["agent_response"]
            assert "真实引用会被验证" in ar, ar
            assert "编造引用仍会保留" in ar, ar
            assert "quote_status=verified; 连字符差异应被容忍" in ar, ar
            assert "quote_status=unverified; 这句不在原文" in ar, ar
            assert f"[quotes.txt](entry:{seeded['eid']}?q=leader+election)" in ar, ar
            assert "entry_id=" not in ar and "quote=\"" not in ar, ar
            print("[2] transcript marks verified and unverified quotes")


async def main() -> None:
    await _create_schema()
    await test_transcript_rewrites_entry_id()
    await test_transcript_marks_quote_verification_status()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
