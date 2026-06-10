"""Deterministic, anchor-preserving compression for read_files results.

This is deliberately not an LLM summarizer. The goal is to reduce large tool
results while keeping the remaining evidence quoteable and reopening omitted
regions possible through read_files locators.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal


Strategy = Literal["text", "pdf_text", "json", "log", "code"]


_PAGE_RE = re.compile(r"(?m)^\[Page ([0-9]+)\](?:\n\[Page label: ([^\]]+)\])?")
_WORD_RE = re.compile(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,}")
_CODE_LINE_RE = re.compile(
    r"^\s*(?:from\s+\S+\s+import\s+|import\s+|class\s+|def\s+|async\s+def\s+|"
    r"function\s+|export\s+|interface\s+|type\s+|struct\s+|enum\s+|impl\s+)"
)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S+|^\s*[A-Z][^.!?]{0,100}:$")
_IMPORTANT_RE = re.compile(
    r"\b(error|exception|traceback|failed|failure|fatal|warn|warning|todo|fixme|"
    r"important|conclusion|summary|result|evidence)\b",
    re.IGNORECASE,
)
_LOG_SIGNAL_RE = re.compile(
    r"\b(error|exception|traceback|failed|failure|fatal|warn|warning|info|debug|trace)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class CompressionSettings:
    enabled: bool = True
    min_chars: int = 12_000
    target_chars: int = 8_000
    context_chars: int = 220


@dataclass(slots=True)
class ReadCompressionResult:
    text: str
    compressed: bool
    strategy: Strategy | None = None
    original_chars: int = 0
    compressed_chars: int = 0
    omitted: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "compressed": self.compressed,
            "strategy": self.strategy,
            "original_chars": self.original_chars,
            "compressed_chars": self.compressed_chars,
            "tokens_saved_estimate": max(0, self.original_chars - self.compressed_chars) // 4,
            "omitted": self.omitted,
            "lossy": self.compressed,
            "quote_safe": (
                "Cite only exact text still visible in `text`; reopen omitted anchors "
                "with read_files before quoting them."
            ),
            "note": self.note,
        }


@dataclass(slots=True)
class _Unit:
    text: str
    start: int
    end: int
    kind: str = "chars"
    page_start: int | None = None
    page_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    score: float = 0.0

    @property
    def length(self) -> int:
        return len(self.text)


def compress_read_text(
    text: str,
    *,
    entry_id: str,
    args: dict[str, Any],
    extras: dict[str, Any] | None = None,
    pipeline: str = "",
    kind: str = "",
    query: str = "",
    settings: CompressionSettings | None = None,
) -> ReadCompressionResult:
    """Compress a read_files text result, preserving reopen anchors.

    The function is fail-open: if a strategy cannot safely reduce the text, it
    returns the original text with ``compressed=False``.
    """
    cfg = settings or CompressionSettings()
    original_len = len(text or "")
    if not cfg.enabled or not text or original_len < cfg.min_chars:
        return ReadCompressionResult(text=text, compressed=False, original_chars=original_len)
    if args.get("compress") is False:
        return ReadCompressionResult(text=text, compressed=False, original_chars=original_len)
    if _is_precision_read(args, extras or {}):
        return ReadCompressionResult(text=text, compressed=False, original_chars=original_len)

    terms = _query_terms(query, str(args.get("heading") or ""), str(args.get("section_id") or ""))
    strategy = _choose_strategy(text, pipeline=pipeline, kind=kind)
    try:
        if strategy == "json":
            result = _compress_json(text, entry_id=entry_id, args=args, terms=terms, cfg=cfg)
        elif strategy == "log":
            result = _compress_lines(
                text,
                entry_id=entry_id,
                args=args,
                extras=extras or {},
                terms=terms,
                cfg=cfg,
                strategy="log",
                context_lines=_context_lines(cfg, default=2, approx_chars=100),
            )
        elif strategy == "code":
            result = _compress_lines(
                text,
                entry_id=entry_id,
                args=args,
                extras=extras or {},
                terms=terms,
                cfg=cfg,
                strategy="code",
                context_lines=_context_lines(cfg, default=1, approx_chars=160),
            )
        elif strategy == "pdf_text":
            result = _compress_pdf_text(text, entry_id=entry_id, args=args, terms=terms, cfg=cfg)
        else:
            result = _compress_text(text, entry_id=entry_id, args=args, extras=extras or {}, terms=terms, cfg=cfg)
    except Exception:
        return ReadCompressionResult(text=text, compressed=False, original_chars=original_len)

    if not result.compressed:
        return result
    if result.compressed_chars >= int(original_len * 0.9):
        return ReadCompressionResult(text=text, compressed=False, original_chars=original_len)
    return result


def _is_precision_read(args: dict[str, Any], extras: dict[str, Any]) -> bool:
    if args.get("question") or extras.get("vlm_used"):
        return True
    if args.get("pattern") or args.get("patterns") or extras.get("hits"):
        return True
    if args.get("line_start") or args.get("line_end"):
        return True
    if args.get("paragraph_start") or args.get("paragraph_end"):
        return True
    return False


def _choose_strategy(text: str, *, pipeline: str, kind: str) -> Strategy:
    p = (pipeline or "").lower()
    k = (kind or "").lower()
    if p == "pdf" or text.lstrip().startswith("[Page "):
        return "pdf_text"
    if p == "log" or k == "log":
        return "log"
    if p in {"text", "archive"} or k in {"text", "docx"}:
        stripped = text.lstrip()
        if stripped.startswith(("{", "[")) and _looks_json(text):
            return "json"
        if _looks_like_code(text):
            return "code"
    if _looks_json(text):
        return "json"
    if _looks_like_code(text):
        return "code"
    if _looks_like_log(text):
        return "log"
    return "text"


def _looks_json(text: str) -> bool:
    try:
        json.loads(text)
    except Exception:
        return False
    return True


def _looks_like_code(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    hits = sum(1 for line in lines[:200] if _CODE_LINE_RE.search(line))
    brace_lines = sum(1 for line in lines[:200] if "{" in line or "}" in line or line.rstrip().endswith(":"))
    return hits >= 3 or (hits >= 1 and brace_lines >= 8)


def _looks_like_log(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 20:
        return False
    levelish = sum(1 for line in lines[:300] if _LOG_SIGNAL_RE.search(line))
    timestamped = sum(1 for line in lines[:300] if re.match(r"\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2}\s+\d{1,2}", line))
    return levelish >= 3 or timestamped >= 8


def _query_terms(*texts: str) -> set[str]:
    terms: set[str] = set()
    for text in texts:
        for match in _WORD_RE.findall(text or ""):
            term = match.casefold()
            if len(term) >= 2:
                terms.add(term)
    return terms


def _score_text(text: str, terms: set[str]) -> float:
    folded = text.casefold()
    score = 0.0
    if terms:
        score += sum(5.0 for term in terms if term in folded)
    if _HEADING_RE.search(text):
        score += 2.5
    if _IMPORTANT_RE.search(text):
        score += 4.0
    if len(text.strip()) < 80:
        score += 0.2
    return score


def _context_lines(cfg: CompressionSettings, *, default: int, approx_chars: int) -> int:
    try:
        context_chars = int(cfg.context_chars)
    except (TypeError, ValueError):
        return default
    if context_chars <= 0:
        return default
    return max(1, min(8, round(context_chars / max(1, approx_chars))))


def _clip_selected_units(
    selected: list[_Unit],
    *,
    entry_id: str,
    args: dict[str, Any],
    terms: set[str],
    cfg: CompressionSettings,
    marker_kind: Literal["page", "chars"],
    base_offset: int = 0,
) -> list[dict[str, Any]]:
    try:
        context_chars = int(cfg.context_chars)
    except (TypeError, ValueError):
        context_chars = 0
    if context_chars <= 0:
        return []
    context_chars = max(80, context_chars)

    omitted: list[dict[str, Any]] = []
    for unit in selected:
        if unit.length <= context_chars * 4:
            continue
        span = _best_clip_span(unit.text, terms)
        if span is None:
            continue
        clip_start = max(0, span[0] - context_chars)
        clip_end = min(len(unit.text), span[1] + context_chars)
        clip_start = _line_floor(unit.text, clip_start)
        clip_end = _line_ceil(unit.text, clip_end)
        if clip_start <= 0 and clip_end >= len(unit.text):
            continue
        if clip_end - clip_start >= int(unit.length * 0.75):
            continue

        parts: list[str] = []
        if clip_start > 0:
            marker, spec = _unit_char_omission_marker(
                unit,
                entry_id=entry_id,
                args=args,
                marker_kind=marker_kind,
                rel_start=0,
                rel_end=clip_start,
                base_offset=base_offset,
            )
            parts.append(marker)
            omitted.append(spec)
        parts.append(unit.text[clip_start:clip_end].strip())
        if clip_end < len(unit.text):
            marker, spec = _unit_char_omission_marker(
                unit,
                entry_id=entry_id,
                args=args,
                marker_kind=marker_kind,
                rel_start=clip_end,
                rel_end=len(unit.text),
                base_offset=base_offset,
            )
            parts.append(marker)
            omitted.append(spec)
        unit.text = "\n\n".join(part for part in parts if part)
    return omitted


def _best_clip_span(text: str, terms: set[str]) -> tuple[int, int] | None:
    folded = text.casefold()
    for term in sorted(terms, key=len, reverse=True):
        idx = folded.find(term)
        if idx >= 0:
            return idx, idx + len(term)
    match = _IMPORTANT_RE.search(text) or _HEADING_RE.search(text)
    if match:
        return match.start(), match.end()
    return None


def _line_floor(text: str, pos: int) -> int:
    if pos <= 0:
        return 0
    prev = text.rfind("\n", 0, pos)
    if prev >= 0:
        return prev + 1
    prev_space = text.rfind(" ", 0, pos)
    return 0 if prev_space < 0 else prev_space + 1


def _line_ceil(text: str, pos: int) -> int:
    if pos >= len(text):
        return len(text)
    nxt = text.find("\n", pos)
    if nxt >= 0:
        return nxt
    next_space = text.find(" ", pos)
    return len(text) if next_space < 0 else next_space


def _unit_char_omission_marker(
    unit: _Unit,
    *,
    entry_id: str,
    args: dict[str, Any],
    marker_kind: Literal["page", "chars"],
    rel_start: int,
    rel_end: int,
    base_offset: int,
) -> tuple[str, dict[str, Any]]:
    omitted_len = max(1, rel_end - rel_start)
    read_len = min(16_000, omitted_len)
    continuation = "; continue with next_offset if truncated" if read_len < omitted_len else ""
    if marker_kind == "page" and unit.page_start is not None and unit.page_end is not None:
        read_args = _reopen_args(
            args,
            {
                "page_start": unit.page_start,
                "page_end": unit.page_end,
                "offset": rel_start,
                "max_chars": read_len,
            },
        )
        spec = {
            "kind": "page_chars",
            "entry_id": entry_id,
            "page_start": unit.page_start,
            "page_end": unit.page_end,
            "offset": rel_start,
            "max_chars": read_len,
            "omitted_chars": omitted_len,
            "read_files_args": read_args,
        }
        marker = (
            f"[...omitted page {unit.page_start} chars {rel_start}-{rel_end}; "
            f"reopen with read_files {_format_reopen_hint(read_args)}{continuation}...]"
        )
        return marker, spec

    offset = base_offset + unit.start + rel_start
    read_args = _reopen_args(args, {"offset": offset, "max_chars": read_len})
    spec = {
        "kind": "chars",
        "entry_id": entry_id,
        "offset": offset,
        "max_chars": read_len,
        "omitted_chars": omitted_len,
        "read_files_args": read_args,
    }
    marker = (
        f"[...omitted chars {offset}-{offset + omitted_len}; "
        f"reopen with read_files {_format_reopen_hint(read_args)}{continuation}...]"
    )
    return marker, spec


def _compress_pdf_text(
    text: str,
    *,
    entry_id: str,
    args: dict[str, Any],
    terms: set[str],
    cfg: CompressionSettings,
) -> ReadCompressionResult:
    pages = _pdf_page_units(text)
    if not pages:
        return _compress_text(text, entry_id=entry_id, args=args, extras={}, terms=terms, cfg=cfg)
    for idx, unit in enumerate(pages):
        unit.score = _score_text(unit.text, terms)
        if idx == 0:
            unit.score += 1.2
        if idx == len(pages) - 1:
            unit.score += 0.8
    selected = _select_units(pages, target_chars=cfg.target_chars)
    if not selected or len(selected) == len(pages):
        return ReadCompressionResult(text=text, compressed=False, strategy="pdf_text", original_chars=len(text))
    internal_omitted = _clip_selected_units(
        selected,
        entry_id=entry_id,
        args=args,
        terms=terms,
        cfg=cfg,
        marker_kind="page",
    )
    compressed, omitted = _join_units_with_markers(
        selected,
        pages,
        entry_id=entry_id,
        args=args,
        marker_kind="page",
    )
    return ReadCompressionResult(
        text=compressed,
        compressed=True,
        strategy="pdf_text",
        original_chars=len(text),
        compressed_chars=len(compressed),
        omitted=internal_omitted + omitted,
        note="PDF pages were sampled; omitted page ranges can be reopened with read_files.",
    )


def _pdf_page_units(text: str) -> list[_Unit]:
    matches = list(_PAGE_RE.finditer(text))
    if not matches:
        return []
    units: list[_Unit] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        page = int(match.group(1))
        units.append(_Unit(
            text=text[start:end].strip(),
            start=start,
            end=end,
            kind="page",
            page_start=page,
            page_end=page,
        ))
    return units


def _compress_text(
    text: str,
    *,
    entry_id: str,
    args: dict[str, Any],
    extras: dict[str, Any],
    terms: set[str],
    cfg: CompressionSettings,
) -> ReadCompressionResult:
    units = _paragraph_units(text)
    if len(units) <= 3:
        return ReadCompressionResult(text=text, compressed=False, strategy="text", original_chars=len(text))
    for idx, unit in enumerate(units):
        unit.score = _score_text(unit.text, terms)
        if idx == 0:
            unit.score += 1.0
        if idx == len(units) - 1:
            unit.score += 0.6
    selected = _select_units(units, target_chars=cfg.target_chars)
    if not selected or len(selected) == len(units):
        return ReadCompressionResult(text=text, compressed=False, strategy="text", original_chars=len(text))
    base_offset = int(extras.get("offset") or args.get("offset") or 0)
    internal_omitted = _clip_selected_units(
        selected,
        entry_id=entry_id,
        args=args,
        terms=terms,
        cfg=cfg,
        marker_kind="chars",
        base_offset=base_offset,
    )
    compressed, omitted = _join_units_with_markers(
        selected,
        units,
        entry_id=entry_id,
        args=args,
        marker_kind="chars",
        base_offset=base_offset,
    )
    return ReadCompressionResult(
        text=compressed,
        compressed=True,
        strategy="text",
        original_chars=len(text),
        compressed_chars=len(compressed),
        omitted=internal_omitted + omitted,
        note="Text paragraphs were sampled; omitted char ranges can be reopened with read_files.",
    )


def _paragraph_units(text: str) -> list[_Unit]:
    parts: list[_Unit] = []
    start = 0
    for match in re.finditer(r"\n\s*\n", text):
        end = match.start()
        body = text[start:end].strip()
        if body:
            parts.append(_Unit(text=body, start=start, end=end))
        start = match.end()
    tail = text[start:].strip()
    if tail:
        parts.append(_Unit(text=tail, start=start, end=len(text)))
    if len(parts) < 4:
        return _line_group_units(text, group_size=8)
    return parts


def _compress_lines(
    text: str,
    *,
    entry_id: str,
    args: dict[str, Any],
    extras: dict[str, Any],
    terms: set[str],
    cfg: CompressionSettings,
    strategy: Literal["log", "code"],
    context_lines: int,
) -> ReadCompressionResult:
    lines = text.splitlines()
    if len(lines) < 20:
        return ReadCompressionResult(text=text, compressed=False, strategy=strategy, original_chars=len(text))
    keep = _select_line_indexes(lines, terms=terms, strategy=strategy, context_lines=context_lines)
    if not keep:
        return ReadCompressionResult(text=text, compressed=False, strategy=strategy, original_chars=len(text))

    while _lines_length(lines, keep) > cfg.target_chars and len(keep) > 20:
        removable = [
            idx for idx in sorted(keep)
            if idx >= 8 and idx < len(lines) - 8 and not _must_keep_line(lines[idx], strategy)
        ]
        if not removable:
            break
        for idx in removable[::2]:
            keep.discard(idx)
            if _lines_length(lines, keep) <= cfg.target_chars:
                break

    compressed, omitted = _join_lines_with_markers(
        lines,
        keep,
        entry_id=entry_id,
        args=args,
        extras=extras,
    )
    return ReadCompressionResult(
        text=compressed,
        compressed=True,
        strategy=strategy,
        original_chars=len(text),
        compressed_chars=len(compressed),
        omitted=omitted,
        note=f"{strategy} lines were sampled; omitted line ranges can be reopened with read_files.",
    )


def _select_line_indexes(
    lines: list[str],
    *,
    terms: set[str],
    strategy: Literal["log", "code"],
    context_lines: int,
) -> set[int]:
    keep: set[int] = set(range(min(12, len(lines))))
    keep.update(range(max(0, len(lines) - 16), len(lines)))
    for idx, line in enumerate(lines):
        folded = line.casefold()
        important = _IMPORTANT_RE.search(line)
        term_hit = any(term in folded for term in terms)
        code_hit = strategy == "code" and _CODE_LINE_RE.search(line)
        if important or term_hit or code_hit:
            for pos in range(max(0, idx - context_lines), min(len(lines), idx + context_lines + 1)):
                keep.add(pos)
    return keep


def _must_keep_line(line: str, strategy: str) -> bool:
    if _IMPORTANT_RE.search(line):
        return True
    if strategy == "code" and _CODE_LINE_RE.search(line):
        return True
    return False


def _lines_length(lines: list[str], keep: set[int]) -> int:
    return sum(len(lines[idx]) + 1 for idx in keep)


def _line_group_units(text: str, *, group_size: int) -> list[_Unit]:
    units: list[_Unit] = []
    offset = 0
    lines = text.splitlines(keepends=True)
    for start_idx in range(0, len(lines), group_size):
        group = lines[start_idx:start_idx + group_size]
        body = "".join(group).strip()
        start = offset
        end = offset + sum(len(line) for line in group)
        offset = end
        if body:
            units.append(_Unit(
                text=body,
                start=start,
                end=end,
                line_start=start_idx + 1,
                line_end=start_idx + len(group),
            ))
    return units


def _compress_json(
    text: str,
    *,
    entry_id: str,
    args: dict[str, Any],
    terms: set[str],
    cfg: CompressionSettings,
) -> ReadCompressionResult:
    try:
        parsed = json.loads(text)
    except Exception:
        return ReadCompressionResult(text=text, compressed=False, strategy="json", original_chars=len(text))
    if isinstance(parsed, list):
        out, omitted = _compress_json_list(parsed, entry_id=entry_id, args=args, terms=terms, cfg=cfg)
    elif isinstance(parsed, dict):
        out, omitted = _compress_json_dict(parsed, entry_id=entry_id, args=args, terms=terms, cfg=cfg)
    else:
        return ReadCompressionResult(text=text, compressed=False, strategy="json", original_chars=len(text))
    if not omitted:
        return ReadCompressionResult(text=text, compressed=False, strategy="json", original_chars=len(text))
    rendered = json.dumps(out, ensure_ascii=False, indent=2)
    return ReadCompressionResult(
        text=rendered,
        compressed=True,
        strategy="json",
        original_chars=len(text),
        compressed_chars=len(rendered),
        omitted=omitted,
        note="JSON was sampled by key/item relevance; reopen original char range for exact data.",
    )


def _compress_json_list(
    items: list[Any],
    *,
    entry_id: str,
    args: dict[str, Any],
    terms: set[str],
    cfg: CompressionSettings,
) -> tuple[list[Any], list[dict[str, Any]]]:
    if len(items) <= 12:
        return items, []
    scored: list[tuple[float, int, Any]] = []
    for idx, item in enumerate(items):
        body = json.dumps(item, ensure_ascii=False, default=str)
        score = _score_text(body, terms)
        if idx < 4 or idx >= len(items) - 4:
            score += 1.0
        scored.append((score, idx, item))
    keep = {idx for _score, idx, _item in sorted(scored, key=lambda t: (-t[0], t[1]))[:20]}
    keep.update(range(min(4, len(items))))
    keep.update(range(max(0, len(items) - 4), len(items)))
    out: list[Any] = []
    omitted: list[dict[str, Any]] = []
    skipped_start: int | None = None
    for idx, item in enumerate(items):
        if idx in keep:
            if skipped_start is not None:
                omitted.append(_json_omission(entry_id, args, skipped_start, idx - 1))
                out.append({"_marginalia_omitted_items": f"{skipped_start}-{idx - 1}"})
                skipped_start = None
            out.append(item)
        elif skipped_start is None:
            skipped_start = idx
    if skipped_start is not None:
        omitted.append(_json_omission(entry_id, args, skipped_start, len(items) - 1))
        out.append({"_marginalia_omitted_items": f"{skipped_start}-{len(items) - 1}"})
    return out, omitted


def _compress_json_dict(
    obj: dict[str, Any],
    *,
    entry_id: str,
    args: dict[str, Any],
    terms: set[str],
    cfg: CompressionSettings,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    keys = list(obj)
    if len(keys) <= 20:
        return obj, []
    scored = []
    for idx, key in enumerate(keys):
        body = key + " " + json.dumps(obj[key], ensure_ascii=False, default=str)
        score = _score_text(body, terms)
        if idx < 5 or idx >= len(keys) - 5:
            score += 1.0
        scored.append((score, idx, key))
    keep = {key for _score, _idx, key in sorted(scored, key=lambda t: (-t[0], t[1]))[:24]}
    out = {key: obj[key] for key in keys if key in keep}
    omitted_keys = [key for key in keys if key not in keep]
    if omitted_keys:
        out["_marginalia_omitted_keys"] = omitted_keys[:80]
    return out, [{
        "kind": "json_keys",
        "entry_id": entry_id,
        "count": len(omitted_keys),
        "read_files_args": _reopen_args(args),
    }] if omitted_keys else []


def _json_omission(entry_id: str, args: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    return {
        "kind": "json_items",
        "entry_id": entry_id,
        "item_start": start,
        "item_end": end,
        "read_files_args": _reopen_args(args),
    }


def _select_units(units: list[_Unit], *, target_chars: int) -> list[_Unit]:
    if not units:
        return []
    ordered = sorted(enumerate(units), key=lambda t: (-t[1].score, t[0]))
    selected: set[int] = {0, len(units) - 1}
    total = units[0].length + (units[-1].length if len(units) > 1 else 0)
    for idx, unit in ordered:
        if idx in selected:
            continue
        if total >= target_chars and unit.score <= 0:
            break
        selected.add(idx)
        total += unit.length
        if total >= target_chars:
            break
    return [units[idx] for idx in sorted(selected)]


def _join_units_with_markers(
    selected: list[_Unit],
    all_units: list[_Unit],
    *,
    entry_id: str,
    args: dict[str, Any],
    marker_kind: Literal["page", "chars"],
    base_offset: int = 0,
) -> tuple[str, list[dict[str, Any]]]:
    selected_ids = {id(unit) for unit in selected}
    parts: list[str] = []
    omitted: list[dict[str, Any]] = []
    pending: list[_Unit] = []

    def flush_pending() -> None:
        if not pending:
            return
        first, last = pending[0], pending[-1]
        if marker_kind == "page" and first.page_start is not None and last.page_end is not None:
            read_args = _reopen_args(
                args,
                {
                    "page_start": first.page_start,
                    "page_end": last.page_end,
                    "max_chars": min(16_000, sum(unit.length for unit in pending)),
                },
            )
            spec = {
                "kind": "page",
                "entry_id": entry_id,
                "page_start": first.page_start,
                "page_end": last.page_end,
                "read_files_args": read_args,
            }
            marker = (
                f"[...omitted pages {first.page_start}-{last.page_end}; "
                f"reopen with read_files {_format_reopen_hint(read_args)}...]"
            )
        else:
            offset = base_offset + first.start
            omitted_len = max(1, last.end - first.start)
            max_chars = min(16_000, omitted_len)
            read_args = _reopen_args(
                args,
                {"offset": offset, "max_chars": max_chars},
            )
            spec = {
                "kind": "chars",
                "entry_id": entry_id,
                "offset": offset,
                "max_chars": max_chars,
                "omitted_chars": omitted_len,
                "read_files_args": read_args,
            }
            continuation = "; continue with next_offset if truncated" if max_chars < omitted_len else ""
            marker = (
                f"[...omitted chars {offset}-{offset + omitted_len}; "
                f"reopen with read_files {_format_reopen_hint(read_args)}{continuation}...]"
            )
        omitted.append(spec)
        parts.append(marker)
        pending.clear()

    for unit in all_units:
        if id(unit) in selected_ids:
            flush_pending()
            parts.append(unit.text)
        else:
            pending.append(unit)
    flush_pending()
    return "\n\n".join(part for part in parts if part), omitted


def _join_lines_with_markers(
    lines: list[str],
    keep: set[int],
    *,
    entry_id: str,
    args: dict[str, Any],
    extras: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    parts: list[str] = []
    omitted: list[dict[str, Any]] = []
    line_base = int(extras.get("line_start") or args.get("line_start") or 1)
    idx = 0
    while idx < len(lines):
        if idx in keep:
            parts.append(lines[idx])
            idx += 1
            continue
        start = idx
        while idx < len(lines) and idx not in keep:
            idx += 1
        end = idx - 1
        line_start = line_base + start
        line_end = line_base + end
        read_args = _reopen_args(
            args,
            {
                "line_start": line_start,
                "line_end": line_end,
                "max_chars": min(
                    16_000,
                    sum(len(lines[pos]) + 1 for pos in range(start, end + 1)),
                ),
            },
        )
        parts.append(
            f"[...omitted lines {line_start}-{line_end}; "
            f"reopen with read_files {_format_reopen_hint(read_args)}...]"
        )
        omitted.append({
            "kind": "lines",
            "entry_id": entry_id,
            "line_start": line_start,
            "line_end": line_end,
            "read_files_args": read_args,
        })
    return "\n".join(parts), omitted


def _reopen_args(
    args: dict[str, Any],
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "member_path" in args:
        out["member_path"] = args["member_path"]
    if updates is None:
        for key in (
            "offset", "max_chars", "page_start", "page_end", "page_label",
            "line_start", "line_end", "section_id", "heading",
        ):
            if key in args:
                out[key] = args[key]
    elif "offset" in updates:
        for key in ("section_id", "heading", "page_label"):
            if key in args:
                out[key] = args[key]
    out.update(updates or {})
    out["compress"] = False
    return out


def _format_reopen_hint(args: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "member_path", "page_start", "page_end", "line_start", "line_end",
        "offset", "max_chars", "section_id", "heading", "page_label", "compress",
    ):
        if key not in args:
            continue
        value = args[key]
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, str):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return " ".join(parts)
