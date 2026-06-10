"""search_journal - DESIGN.md section 10.1.

Low-level journal lookup for focused follow-up and debugging.

The journal is two tiers in one table (see [[journal-tiers]]):
  - `insight`: durable cross-session distillations.
  - `reflect_turn`: per-turn bullets from one specific session.

Defaults to `kinds=["insight", "reflect_turn"]` so a fresh user message can
see both durable memory and recent per-turn breadcrumbs. Pass
`kinds=["insight"]` for durable-only recall, or `kinds=["reflect_turn"]`
together with a `conversation_id` to skim one session.

Superseded insight rows (whose `superseded_by_id IS NOT NULL`) are hidden by
default; the chain replacement is the answer. Set `include_superseded=true`
to see history.

Invalidated rows (contradicted by later reflect_turn output) are also hidden
by default. Set `include_invalidated=true` for audit/debugging.

Text lookup accepts a string or an array. Multi-term text is ORed, so broad
recall should use `text=["term1", "term2"]` instead of one packed phrase.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from marginalia.agent.text_query import normalize_text_queries
from marginalia.agent.tools import ToolContext, tool
from marginalia.db.models import Journal
from marginalia.repositories import entries as entries_repo
from marginalia.repositories import journal as journal_repo


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [],
    "properties": {
        "text": {
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ],
            "description": (
                "One query string or an array of query terms/phrases. Array "
                "items are ORed against journal notes. For multi-keyword "
                "fallback after tags, prefer an array."
            ),
        },
        "entry_id": {
            "type": "string",
            "description": (
                "Only return notes whose entry_ids list includes this id. "
                "Must be a UUID or short hex prefix (>= 8 chars), NOT a file name."
            ),
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Match notes carrying ANY of these tags (OR).",
        },
        "kinds": {
            "type": "array",
            "items": {"type": "string", "enum": ["insight", "reflect_turn"]},
            "description": (
                "Which journal tiers to search. Default "
                "['insight', 'reflect_turn']: both durable "
                "cross-session memory and per-turn bullets."
            ),
        },
        "conversation_id": {
            "type": "string",
            "description": "Restrict to notes attached to this conversation.",
        },
        "include_superseded": {
            "type": "boolean",
            "description": (
                "If true, include insight rows that have been replaced by a "
                "newer version. Default false; only the current version of "
                "each chain is returned."
            ),
        },
        "include_invalidated": {
            "type": "boolean",
            "description": (
                "If true, include journal rows hidden because a later "
                "reflection found them contradicted. Default false."
            ),
        },
        "since_days": {
            "type": "integer",
            "minimum": 1,
            "maximum": 365,
            "description": "Limit to notes written within the last N days. Default 90.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "description": "Max notes returned. Default 10.",
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Skip first N matches that satisfy all filters (text + "
                "entry_id + any tag + kinds + conversation + since/super). "
                "Default 0. Use with `next_offset` to page."
            ),
        },
        "order": {
            "type": "string",
            "enum": ["recent_first", "oldest_first"],
            "description": "Default 'recent_first'.",
        },
    },
}


@tool(
    name="search_journal",
    description=(
        "Low-level journal lookup for focused follow-up, prior-work checks, "
        "or debugging prior notes. Searches durable "
        "insights and reflect_turn notes by default. Text array terms are "
        "ORed; other filters narrow the result."
    ),
    schema=SCHEMA,
)
async def search_journal(
    db: AsyncSession,
    ctx: ToolContext,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    return await run_search_journal(db, args, match="all")


async def run_search_journal(
    db: AsyncSession,
    args: Mapping[str, Any],
    *,
    match: str = "all",
) -> dict[str, Any]:
    """Shared implementation for the public tool and recall wrappers.

    Public `search_journal` keeps the historical "all filters must match"
    contract. `recall_knowledge` uses `match="any"` so text/tag seeds widen
    the journal pass without adding a public schema knob.
    """
    text_q = normalize_text_queries(args.get("text")) or None
    entry_id = args.get("entry_id")
    tags = args.get("tags") or []
    kinds = list(args.get("kinds") or ["insight", "reflect_turn"])
    conversation_id = args.get("conversation_id")
    include_superseded = bool(args.get("include_superseded") or False)
    include_invalidated = bool(args.get("include_invalidated") or False)
    since_days = int(args.get("since_days") or 90)
    limit = min(int(args.get("limit") or 10), 50)
    offset = max(0, int(args.get("offset") or 0))
    order = args.get("order") or "recent_first"

    resolved_entry_id = str(entry_id).strip() if entry_id else None
    if resolved_entry_id:
        resolved_entry_id, err = await entries_repo.resolve_entry_id_prefix(
            db, resolved_entry_id,
        )
        if err:
            return {
                "notes": [],
                "count": 0,
                "has_more": False,
                "entry_id_error": err,
            }

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    python_text_filter = match == "any" and bool(text_q) and bool(tags)
    repo_text_q = None if python_text_filter else text_q

    # The journal's JSON filters (entry_id + tags) cannot be expressed in
    # SQLite cleanly, so we post-filter in Python. To honor a true offset
    # we walk the SQL window forward (chunks of `limit*4`) until we have
    # collected `offset + limit` post-filtered hits or exhausted rows.
    needed = offset + limit
    collected: list[Journal] = []
    cursor = 0
    chunk = max(limit * 4, 20)
    exhausted = False
    while len(collected) < needed:
        rows = await journal_repo.search(
            db,
            cutoff=cutoff,
            kinds=kinds,
            conversation_id=conversation_id,
            include_superseded=include_superseded,
            include_invalidated=include_invalidated,
            text=repo_text_q,
            order=order,
            limit=chunk,
            offset=cursor,
        )
        if not rows:
            exhausted = True
            break
        for j in rows:
            if resolved_entry_id and resolved_entry_id not in (j.entry_ids or []):
                continue
            note_tags = set(j.tags or [])
            if match == "any" and (text_q or tags):
                text_ok = bool(text_q) and _note_matches_text(j.note, text_q)
                tags_ok = bool(tags) and bool(note_tags.intersection(tags))
                if not (text_ok or tags_ok):
                    continue
            elif tags:
                if not note_tags.intersection(tags):
                    continue
            collected.append(j)
            if len(collected) >= needed:
                break
        cursor += len(rows)
        if len(rows) < chunk:
            exhausted = True
            break

    annotated = await _annotate_journal_validity(db, collected)
    annotated = _downgrade_stale_notes(annotated)
    page = annotated[offset: offset + limit]
    has_more = (not exhausted) or (len(annotated) > offset + len(page))
    out: dict[str, Any] = {
        "notes": page,
        "count": len(page),
        "has_more": has_more,
    }
    if has_more:
        out["next_offset"] = offset + len(page)
    return out


def _note_matches_text(note: str | None, terms: list[str]) -> bool:
    haystack = (note or "").casefold()
    return any(term.casefold() in haystack for term in terms)


async def _annotate_journal_validity(
    db: AsyncSession,
    rows: list[Journal],
) -> list[dict[str, Any]]:
    all_entry_ids: list[str] = []
    for row in rows:
        for entry_id in row.entry_ids or []:
            if entry_id and entry_id not in all_entry_ids:
                all_entry_ids.append(str(entry_id))
    statuses = await entries_repo.list_journal_reference_statuses(db, all_entry_ids)
    return [_journal_note_to_dict(row, statuses) for row in rows]


def _journal_note_to_dict(
    row: Journal,
    statuses: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    entry_validity = _journal_entry_validity(row, statuses)
    invalidated_at = getattr(row, "invalidated_at", None)
    note = {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "note": row.note,
        "entry_ids": list(row.entry_ids or []),
        "tags": list(row.tags or []),
        "source_kind": row.source_kind,
        "superseded_by_id": row.superseded_by_id,
        "invalidated_at": invalidated_at.isoformat() if invalidated_at else None,
        "invalidated_by_id": getattr(row, "invalidated_by_id", None),
        "invalidated_reason": getattr(row, "invalidated_reason", None),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "entry_validity": entry_validity,
    }
    if entry_validity["status"] == "stale":
        note["validity_note"] = "引用实体已变更"
    return note


def _journal_entry_validity(
    row: Journal,
    statuses: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    entry_ids = [str(entry_id) for entry_id in row.entry_ids or [] if entry_id]
    entries: list[dict[str, Any]] = []
    stale_reasons: list[str] = []
    stale_entry_ids: list[str] = []
    for entry_id in entry_ids:
        status = statuses.get(entry_id)
        if status is None:
            reason = "missing"
            item_status = "stale"
        elif status.get("entry_deleted_at") is not None:
            reason = "entry_deleted"
            item_status = "stale"
        elif status.get("file_deleted_at") is not None:
            reason = "file_deleted"
            item_status = "stale"
        elif _file_reingested_after_note(status.get("file_ingested_at"), row.created_at):
            reason = "file_reingested_after_note"
            item_status = "stale"
        else:
            reason = None
            item_status = "current"
        item: dict[str, Any] = {
            "entry_id": entry_id,
            "status": item_status,
        }
        if reason:
            item["reason"] = reason
            stale_entry_ids.append(entry_id)
            if reason not in stale_reasons:
                stale_reasons.append(reason)
        ingested_at = status.get("file_ingested_at") if status else None
        if ingested_at is not None:
            item["file_ingested_at"] = ingested_at.isoformat()
        entries.append(item)
    if not entry_ids:
        status = "unreferenced"
    elif stale_entry_ids:
        status = "stale"
    else:
        status = "current"
    return {
        "status": status,
        "stale_entry_ids": stale_entry_ids,
        "stale_reasons": stale_reasons,
        "entries": entries,
    }


def _file_reingested_after_note(ingested_at: Any, note_created_at: Any) -> bool:
    if ingested_at is None or note_created_at is None:
        return False
    return ingested_at > note_created_at


def _downgrade_stale_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        enumerate(notes),
        key=lambda item: (
            item[1].get("entry_validity", {}).get("status") == "stale",
            item[0],
        ),
    )
    return [note for _idx, note in ordered]
