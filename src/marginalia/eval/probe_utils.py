"""Text, citation, and evidence helpers for answer/report probes."""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from marginalia.eval.utils import _append_unique_str

def _expected_labels(
    metadata: Mapping[str, Any],
    relevant_doc_ids: Iterable[str],
) -> list[str]:
    labels: list[str] = []
    for doc_id in relevant_doc_ids:
        raw = metadata.get(doc_id)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            label = str(item.get("label") or "").strip().upper()
            if label in {"SUPPORT", "CONTRADICT", "INSUFFICIENT"}:
                _append_unique_str(labels, label)
    return labels


_VERDICT_RE = re.compile(
    r"^\s*(?:\*\*)?\s*Verdict\s*:\s*"
    r"(SUPPORT|CONTRADICT|INSUFFICIENT)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _predict_answer_label(answer: str) -> str | None:
    match = _VERDICT_RE.search(answer or "")
    if match:
        return match.group(1).upper()
    text = (answer or "").casefold()
    if "insufficient" in text or "cannot be verified" in text or "not possible to" in text:
        return "INSUFFICIENT"
    if "contradict" in text or "incorrect" in text or "does not support" in text:
        return "CONTRADICT"
    if "support" in text or "accurate" in text or "consistent with" in text:
        return "SUPPORT"
    return None


_CITED_ENTRY_RE = re.compile(
    r"(?:entry_id\s*=\s*`?|entry:)"
    r"([0-9a-fA-F][0-9a-fA-F\-]{6,35})`?"
)


_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_./,-]*")


_QUERY_STOPWORDS = {
    "about",
    "after",
    "against",
    "and",
    "are",
    "consisting",
    "does",
    "from",
    "have",
    "into",
    "larger",
    "than",
    "that",
    "the",
    "their",
    "this",
    "with",
}


def _extract_cited_entry_ids(
    answer: str,
    *,
    known_entry_ids: Iterable[str],
) -> list[str]:
    known = list(known_entry_ids)
    out: list[str] = []
    seen: set[str] = set()
    for match in _CITED_ENTRY_RE.finditer(answer or ""):
        resolved = _resolve_cited_entry_id(match.group(1), known)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _resolve_cited_entry_id(raw: str, known_entry_ids: list[str]) -> str | None:
    raw_clean = raw.strip().strip("`")
    if raw_clean in known_entry_ids:
        return raw_clean
    compact = raw_clean.replace("-", "").lower()
    matches = [
        eid for eid in known_entry_ids
        if eid.replace("-", "").lower().startswith(compact)
    ]
    return matches[0] if len(matches) == 1 else None


def _query_patterns(query: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in _QUERY_TOKEN_RE.findall(query):
        token = raw.strip(".,;:!?()[]{}\"'")
        if not token:
            continue
        key = token.casefold()
        if key in seen or key in _QUERY_STOPWORDS:
            continue
        has_digit = any(ch.isdigit() for ch in token)
        has_upper = any(ch.isupper() for ch in token)
        if len(token) < 4 and not has_digit and not has_upper:
            continue
        seen.add(key)
        out.append(re.escape(token))
        if len(out) >= 8:
            break
    return out


def _collect_read_text(read: Mapping[str, Any], *, max_chars: int) -> str:
    parts: list[str] = []
    used = 0
    for segment in read.get("reads") or []:
        if not isinstance(segment, Mapping):
            continue
        text = segment.get("text")
        if not segment.get("ok") or not text:
            continue
        label = _read_label(segment.get("args") or {})
        block = f"{label}\n{text}" if label else str(text)
        remaining = max_chars - used
        if remaining <= 0:
            break
        parts.append(block[:remaining])
        used += min(len(block), remaining)
    return "\n\n".join(parts).strip()


def _read_label(args: Any) -> str:
    if not isinstance(args, Mapping):
        return ""
    if args.get("pattern"):
        return f"[pattern: {args['pattern']}]"
    if args.get("line_start"):
        end = args.get("line_end") or args.get("line_start")
        return f"[lines {args['line_start']}-{end}]"
    if args.get("offset"):
        return f"[offset {args['offset']}]"
    return "[start]"


def _compact_description(value: Any) -> str:
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        sections = value.get("sections")
        if isinstance(sections, list):
            parts = []
            for section in sections[:5]:
                if not isinstance(section, dict):
                    continue
                title = str(section.get("title") or "").strip()
                summary = str(section.get("summary") or "").strip()
                if title or summary:
                    parts.append(f"{title}: {summary}".strip(": "))
            return "; ".join(parts)
    if isinstance(value, str):
        return value.strip()
    return ""
