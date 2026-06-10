"""LLM prompt rendering and completion calls for eval probes."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from marginalia.eval.utils import _coerce_score, _parse_json_object, _truncate
from marginalia.llm import ChatMessage, ChatRequest, get_chat_client

async def _complete_answer_probe(
    *,
    query: str,
    evidence: list[dict[str, Any]],
    profile: str,
    max_tokens: int,
) -> tuple[str, dict[str, int]]:
    client = get_chat_client(profile)
    resp = await client.complete(ChatRequest(
        system=_ANSWER_PROBE_SYSTEM,
        messages=[
            ChatMessage(
                role="user",
                content=_render_answer_probe_user_prompt(query, evidence),
            ),
        ],
        max_tokens=max_tokens,
        tools=None,
        json_schema=None,
        temperature=0.2,
    ))
    usage = resp.usage
    return resp.text or "", {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_creation_tokens": usage.cache_creation_tokens,
    }


async def _complete_report_probe(
    *,
    query: str,
    evidence: list[dict[str, Any]],
    profile: str,
    max_tokens: int,
) -> tuple[str, dict[str, int]]:
    client = get_chat_client(profile)
    resp = await client.complete(ChatRequest(
        system=_RAG_REPORT_SYSTEM,
        messages=[
            ChatMessage(
                role="user",
                content=_render_report_probe_user_prompt(query, evidence),
            ),
        ],
        max_tokens=max_tokens,
        tools=None,
        json_schema=None,
        temperature=0.2,
    ))
    usage = resp.usage
    return resp.text or "", {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_creation_tokens": usage.cache_creation_tokens,
    }


async def _judge_report_pair(
    *,
    query: str,
    rag_answer: str,
    react_answer: str,
    expected_labels: list[str],
    profile: str,
) -> dict[str, Any]:
    swap = int(hashlib.sha1(query.encode("utf-8")).hexdigest(), 16) % 2 == 1
    answer_a = react_answer if swap else rag_answer
    answer_b = rag_answer if swap else react_answer
    client = get_chat_client(profile)
    try:
        resp = await client.complete(ChatRequest(
            system=_REPORT_JUDGE_SYSTEM,
            messages=[
                ChatMessage(
                    role="user",
                    content=_render_report_judge_prompt(
                        query=query,
                        expected_labels=expected_labels,
                        answer_a=answer_a,
                        answer_b=answer_b,
                    ),
                ),
            ],
            max_tokens=450,
            tools=None,
            json_schema=_REPORT_JUDGE_SCHEMA,
            temperature=0.0,
        ))
        obj = resp.parsed_json or _parse_json_object(resp.text or "")
        raw_winner = str(obj.get("winner") or "tie").lower()
        if raw_winner not in {"a", "b", "tie"}:
            raw_winner = "tie"
        winner = "tie"
        if raw_winner == "a":
            winner = "react" if swap else "rag"
        elif raw_winner == "b":
            winner = "rag" if swap else "react"
        scores_obj = obj.get("scores") if isinstance(obj.get("scores"), Mapping) else {}
        scores = {
            "rag": _coerce_score(scores_obj.get("b" if swap else "a")),
            "react": _coerce_score(scores_obj.get("a" if swap else "b")),
        }
        usage = resp.usage
        return {
            "winner": winner,
            "raw_winner": raw_winner,
            "swapped": swap,
            "scores": scores,
            "reason": str(obj.get("reason") or "")[:800],
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_creation_tokens": usage.cache_creation_tokens,
            },
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "winner": "tie",
            "raw_winner": "tie",
            "swapped": swap,
            "scores": {},
            "reason": str(exc)[:800],
            "usage": {},
            "error": repr(exc),
        }


def _render_react_report_user_prompt(query: str) -> str:
    return "\n".join([
        "Investigate the following claim or question using the local knowledge base.",
        "",
        "# Question",
        query.strip(),
        "",
        "# Output",
        "Write a concise investigation report.",
        "If the question is a claim, start with exactly one of:",
        "Verdict: SUPPORT",
        "Verdict: CONTRADICT",
        "Verdict: INSUFFICIENT",
        "",
        "Cover the main supporting evidence, any contradicting or limiting evidence,",
        "and cite factual conclusions with entry_id footnotes.",
    ])


def _render_report_probe_user_prompt(
    query: str,
    evidence: list[dict[str, Any]],
) -> str:
    blocks = [
        "# Question",
        query.strip(),
        "",
        "# Retrieved Evidence",
    ]
    if not evidence:
        blocks.append("(no evidence retrieved)")
    for item in evidence:
        blocks.extend([
            "",
            f"## Candidate {item['rank']}",
            f"doc_id: {item.get('doc_id') or ''}",
            f"entry_id: {item['entry_id']}",
            f"title: {item.get('display_name') or ''}",
        ])
        if item.get("summary"):
            blocks.append(f"summary: {_truncate(str(item['summary']), 700)}")
        if item.get("description"):
            blocks.append(f"description: {_truncate(str(item['description']), 900)}")
        if item.get("text"):
            blocks.extend([
                "source_text:",
                "```",
                str(item["text"]),
                "```",
            ])
        else:
            blocks.append(f"source_text: (not readable: {item.get('read_error') or 'empty'})")
    blocks.extend([
        "",
        "# Task",
        "Write the investigation report now. Do not mention this evaluation harness.",
    ])
    return "\n".join(blocks)


def _render_report_judge_prompt(
    *,
    query: str,
    expected_labels: list[str],
    answer_a: str,
    answer_b: str,
) -> str:
    expected = ", ".join(expected_labels) if expected_labels else "(none supplied)"
    return "\n".join([
        "# User Question",
        query.strip(),
        "",
        "# Gold Verdict",
        expected,
        "",
        "# Answer A",
        answer_a.strip() or "(empty)",
        "",
        "# Answer B",
        answer_b.strip() or "(empty)",
        "",
        "# Judgment Criteria",
        "Prefer the answer that is more useful as a knowledge-base report:",
        "- if a gold verdict is supplied, correctness against it is the first priority",
        "- directly answers the question",
        "- gives a clear conclusion or verdict when applicable",
        "- uses specific evidence and citations",
        "- notes contradictions, uncertainty, or limitations",
        "- avoids unsupported claims and irrelevant detail",
        "",
        "Return JSON only.",
    ])


_RAG_REPORT_SYSTEM = """You are testing a traditional one-shot RAG report path.

Use only the retrieved evidence supplied in the user message. Do not call
tools, infer from outside knowledge, or invent missing facts. If evidence is
insufficient, say that clearly.

If the question is a claim that should be assessed against evidence, start
with exactly one line:
Verdict: SUPPORT
Verdict: CONTRADICT
or
Verdict: INSUFFICIENT

Write a concise investigation report with:
- conclusion
- supporting evidence
- contradicting or limiting evidence when present

Citation rules:
- Cite every factual conclusion using a footnote.
- Footnotes must use this exact shape:
  [^1]: entry_id=<entry_id>, quote="<10-80 copied chars>" - why it supports the answer
- Only cite entry_id values shown in the supplied evidence.
- Do not cite doc_id values; doc_id is evaluation metadata only.
"""


_REPORT_JUDGE_SYSTEM = """You are an impartial evaluator comparing two report answers.

You do not know which system produced each answer. Judge only the report
quality for the given user question. Prefer correctness, evidence use,
citation quality, uncertainty handling, and completeness over style.
Return strict JSON only.
"""


_REPORT_JUDGE_SCHEMA: dict[str, Any] = {
    "title": "ReportPairJudge",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "winner": {
            "type": "string",
            "enum": ["a", "b", "tie"],
        },
        "scores": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "a": {"type": "number", "minimum": 0, "maximum": 10},
                "b": {"type": "number", "minimum": 0, "maximum": 10},
            },
            "required": ["a", "b"],
        },
        "reason": {"type": "string"},
    },
    "required": ["winner", "scores", "reason"],
}


_ANSWER_PROBE_SYSTEM = """You are testing Marginalia's final-answer path.

Answer the question only from the evidence supplied in the user message. If
the evidence is insufficient, say that clearly. Keep the answer concise and
use Markdown.

If the question is a claim that should be assessed against evidence, start
with exactly one line:
Verdict: SUPPORT
Verdict: CONTRADICT
or
Verdict: INSUFFICIENT

Citation rules:
- Cite every factual conclusion using a footnote.
- Footnotes must use this exact shape:
  [^1]: entry_id=<entry_id>, quote="<10-80 copied chars>" - why it supports the answer
- Only cite entry_id values shown in the supplied evidence.
- Do not cite doc_id values; doc_id is evaluation metadata only.
"""


def _render_answer_probe_user_prompt(
    query: str,
    evidence: list[dict[str, Any]],
) -> str:
    blocks = [
        "# Question",
        query.strip(),
        "",
        "# Retrieved Evidence",
    ]
    if not evidence:
        blocks.append("(no evidence retrieved)")
    for item in evidence:
        blocks.extend([
            "",
            f"## Candidate {item['rank']}",
            f"doc_id: {item.get('doc_id') or ''}",
            f"entry_id: {item['entry_id']}",
            f"title: {item.get('display_name') or ''}",
        ])
        if item.get("summary"):
            blocks.append(f"summary: {_truncate(str(item['summary']), 700)}")
        if item.get("description"):
            blocks.append(f"description: {_truncate(str(item['description']), 900)}")
        if item.get("text"):
            blocks.extend([
                "source_text:",
                "```",
                str(item["text"]),
                "```",
            ])
        else:
            blocks.append(f"source_text: (not readable: {item.get('read_error') or 'empty'})")
    blocks.extend([
        "",
        "# Task",
        "Write the final answer now. Do not mention this evaluation harness.",
    ])
    return "\n".join(blocks)
