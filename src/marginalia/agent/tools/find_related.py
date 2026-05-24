"""find_related — design.md §10.x (discovery layer).

Cuts agent loop count: when the agent has identified one relevant entry,
this tool returns its likely neighbours immediately, so the agent doesn't
need to run a second search + read_files cycle to find sibling material.

Backed by services.recommend.find_related — random-walk-with-restart over
the entry_relations graph (cooccurrence + tag_overlap + citation signals).
"""
from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from marginalia.agent.tools import ToolContext, tool
from marginalia.services.recommend import find_related as _find_related


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entry_id"],
    "properties": {
        "entry_id": {
            "type": "string",
            "description": "Seed entry id; recommendations radiate out from this entry.",
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "description": "How many neighbours to return. Default 8.",
        },
    },
}


@tool(
    name="find_related",
    description=(
        "Given a seed entry, return entries the corpus has linked to it "
        "(via tag overlap, journal cooccurrence, or citation graph). "
        "Use this BEFORE running another search when you already know "
        "one relevant entry — it skips a round-trip and surfaces "
        "structurally adjacent material the agent hasn't read yet. "
        "Returns at most top_k entries, ordered by random-walk score. "
        "Empty result means the seed entry has no recorded relations."
    ),
    schema=SCHEMA,
)
async def find_related(
    db: AsyncSession,
    ctx: ToolContext,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    seed = args["entry_id"]
    top_k = int(args.get("top_k") or 8)
    rows = await _find_related(db, seed_entry_id=seed, top_k=top_k)
    return {
        "seed_entry_id": seed,
        "results": [
            {
                "entry_id": r.entry_id,
                "display_name": r.display_name,
                "score": round(r.score, 4),
                "visit_count": r.visit_count,
                "direct_edge_weight": r.direct_edge_weight,
            }
            for r in rows
        ],
        "count": len(rows),
    }
