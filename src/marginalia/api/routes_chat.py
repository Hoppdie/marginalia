"""Chat HTTP route — DESIGN.md §12.2 / plan §5.5.

  POST /chat/{session_id}      — run one user turn as SSE event stream

The agent runtime (`marginalia.agent.runtime.run_turn`) is an async
generator yielding AgentEvent frames. This route wraps it as a proper
text/event-stream response. Each frame becomes one SSE event with
`event:` set to event_type and `data:` carrying the payload.

Event types (see AgentEvent docstring): conversation / planning / plan /
thinking / tool_call / tool_result / answer / error / done.

reflect_turn is enqueued by run_turn at finalize time, before the `done`
event is yielded — there's no separate end-of-turn hook.

## Per-session serialisation

`run_turn` is documented (agent/runtime.py module docstring) as assuming
one in-flight turn per session — concurrent calls race on the
`latest_turn_index() + 1` read-modify-write and silently write two
conversations with the same turn_index.

We enforce that here with a per-session asyncio.Lock, held for the
entire lifetime of the SSE stream. Locks live in a plain dict keyed by
session_id. We don't bother evicting — sessions are coarse and
long-lived (one per UI tab open), and a Lock is ~200 bytes; the
process restarts long before this becomes a memory concern.
(WeakValueDictionary was tried first; it doesn't work because the lock
has no other strong reference between requests, so each call sees a
fresh lock and the serialisation collapses.)

Cross-process safety is the database's job: `conversations` carries
UNIQUE(session_id, turn_index), so a multi-worker Postgres deploy still
fails closed (the second writer hits IntegrityError) instead of
producing duplicate rows.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from marginalia.agent.runtime import run_turn
from marginalia.agent.types import AgentTurnError, ChatMode, RunOptions
from marginalia.config import get_settings
from marginalia.db.models import Session as SessionRow
from marginalia.db.session import get_session, session_scope
from marginalia.repositories import sessions as session_service
from marginalia.repositories.task_outcomes import record_outcome

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)


_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
CLIENT_STOPPED_MESSAGE = "Chat turn was stopped by the client."


def _lock_for(session_id: str) -> asyncio.Lock:
    """Return the lock for `session_id`, creating one on first access.

    Single-threaded asyncio loop: get-or-create is race-free without
    any extra synchronisation.
    """
    lock = _SESSION_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[session_id] = lock
    return lock


class ChatBody(BaseModel):
    query: str
    mode: ChatMode = "auto"


def _timeout_message(timeout_seconds: float) -> str:
    return f"Chat turn exceeded {timeout_seconds:g} seconds and was stopped."


async def _finish_interrupted_turn(
    *,
    session_id: str,
    conversation_id: str | None,
    reason: str,
    message: str,
    fallback_to_latest: bool = False,
) -> None:
    """Finalize an interrupted conversation so replay never shows a stale spinner."""
    async with session_scope() as db:
        conv = None
        if conversation_id:
            conv = await session_service.get_conversation(db, conversation_id)
        if conv is None and fallback_to_latest:
            conv = await session_service.latest_unfinished_conversation(db, session_id)
        if conv is None or conv.session_id != session_id or conv.ended_at is not None:
            return
        await session_service.finalize_conversation(
            db,
            conversation_id=conv.id,
            agent_response=message,
        )
        await record_outcome(
            db,
            task_kind="run_turn",
            object_kind="conversation",
            object_id=conv.id,
            outcome="error",
            detail={
                "session_id": session_id,
                "error": message,
                "interrupted": reason,
            },
        )
        await db.commit()


@router.post("/chat/{session_id}")
async def post_chat(
    session_id: str,
    body: ChatBody,
    db: AsyncSession = Depends(get_session),
) -> Any:
    s = await db.get(SessionRow, session_id)
    if s is None or s.deleted_at is not None:
        raise HTTPException(status_code=404, detail="session not found")
    if s.ended_at is not None:
        await session_service.reopen_session(db, session_id=session_id)
        await db.commit()

    user_message = body.query
    lock = _lock_for(session_id)

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        # Hold the lock for the WHOLE turn — plan + execute + finalize
        # all touch shared per-session state (conversation rows, journal
        # via reflect, session-level counters). Releasing earlier would
        # let a concurrent request see partial state.
        async with lock:
            conversation_id: str | None = None
            timeout_seconds = get_settings().agent_turn_timeout_seconds
            try:
                async def forward() -> AsyncIterator[dict[str, str]]:
                    nonlocal conversation_id
                    async for ev in run_turn(
                        session_id=session_id,
                        user_message=user_message,
                        options=RunOptions(mode=body.mode),
                    ):
                        if ev.event_type == "conversation" and ev.data:
                            conversation_id = ev.data
                        yield {"event": ev.event_type, "data": ev.data}

                if timeout_seconds > 0:
                    async with asyncio.timeout(timeout_seconds):
                        async for frame in forward():
                            yield frame
                else:
                    async for frame in forward():
                        yield frame
            except TimeoutError:
                msg = _timeout_message(timeout_seconds)
                await _finish_interrupted_turn(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    reason="timeout",
                    message=msg,
                    fallback_to_latest=True,
                )
                yield {"event": "error", "data": msg}
            except asyncio.CancelledError:
                await asyncio.shield(
                    _finish_interrupted_turn(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        reason="client_cancelled",
                        message=CLIENT_STOPPED_MESSAGE,
                        fallback_to_latest=True,
                    )
                )
                raise
            except AgentTurnError as exc:
                msg = str(exc)
                await _finish_interrupted_turn(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    reason="agent_error",
                    message=msg,
                )
                yield {"event": "error", "data": msg}
            except Exception as exc:
                log.exception("chat turn failed for session %s", session_id)
                msg = str(exc)
                await _finish_interrupted_turn(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    reason="exception",
                    message=msg,
                    fallback_to_latest=True,
                )
                yield {"event": "error", "data": msg}

    return EventSourceResponse(event_stream())
