"""Built-in model-view compression bridge.

Marginalia keeps the deterministic compression surface it uses in-tree so
release builds do not need a separate compression package. The runtime boundary
remains fail-open: if a local compressor cannot shrink a payload, callers
receive ``None`` and keep the original prompt payload.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from marginalia.config import get_settings

log = logging.getLogger(__name__)

QUERY_TOOLS = {
    "query_log",
    "query_sql",
    "search_metadata",
}

_SEARCH_LINE_RE = re.compile(r"(?m)^[^\s:]+:\d+:")
_CODE_LINE_RE = re.compile(
    r"^\s*(?:from\s+\S+\s+import\s+|import\s+|class\s+|def\s+|async\s+def\s+|"
    r"function\s+|export\s+|interface\s+|type\s+|struct\s+|enum\s+|impl\s+|package\s+)"
)
_LOG_SIGNAL_RE = re.compile(
    r"\b(error|exception|traceback|failed|failure|fatal|warn|warning|info|debug|trace)\b",
    re.IGNORECASE,
)
_JSON_EXTS = {".json", ".jsonl", ".ndjson"}
_TABLE_EXTS = {".csv", ".tsv", ".tab"}
_LOG_EXTS = {".log", ".out", ".err"}
_EXTRACTED_TEXT_EXTS = {".docx", ".pdf"}
_CODE_EXTS = {
    ".py", ".pyw", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".cs", ".rb", ".php", ".swift", ".kt", ".kts", ".scala",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".sql",
    ".lua", ".r", ".jl", ".ex", ".exs", ".erl", ".hrl",
}
_INGEST_TEXT_MIN_CHARS = 24_000
_ARCHIVE_PREVIEW_MIN_CHARS = 900


@dataclass(slots=True)
class CompressedText:
    text: str
    strategy: str
    original_chars: int
    compressed_chars: int
    extra: dict[str, Any]

    def metadata(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "original_chars": self.original_chars,
            "compressed_chars": self.compressed_chars,
            "tokens_saved_estimate": max(0, self.original_chars - self.compressed_chars) // 4,
            **self.extra,
        }


def maybe_compress_tool_result_for_model(
    tool_name: str,
    payload: Any,
    *,
    context: str = "",
) -> dict[str, Any] | None:
    """Return a compact model-only tool payload, or ``None`` to keep original."""
    settings = get_settings()
    if not settings.compression_enabled or tool_name not in QUERY_TOOLS:
        return None

    original_text = _json_text(payload)
    if len(original_text) < settings.compression_min_chars:
        return None

    try:
        compressed = _compress_query_payload(tool_name, payload, context=context)
    except Exception as exc:  # noqa: BLE001 - optional dependency boundary
        log.debug("Query compression skipped for %s: %r", tool_name, exc)
        return None
    if compressed is None or not compressed.text.strip():
        return None

    envelope = _tool_envelope(tool_name, payload, compressed)
    envelope_text = _json_text(envelope)
    if not _beats_threshold(
        original_chars=len(original_text),
        compressed_chars=len(envelope_text),
        max_ratio=settings.compression_max_ratio,
    ):
        return None
    return envelope


def maybe_compress_ingest_view(
    body: str,
    *,
    kind: str,
    context: str = "",
) -> tuple[str, dict[str, Any] | None]:
    """Compress ingest prompt views for low-risk content classes."""
    settings = get_settings()
    if not settings.compression_enabled or len(body) < settings.compression_min_chars:
        return body, None

    try:
        compressed = _compress_ingest_text(body, kind=kind, context=context)
    except Exception as exc:  # noqa: BLE001 - optional dependency boundary
        log.debug("Ingest compression skipped for %s: %r", kind, exc)
        return body, None
    if compressed is None or not compressed.text.strip():
        return body, None
    if not _beats_threshold(
        original_chars=len(body),
        compressed_chars=len(compressed.text),
        max_ratio=settings.compression_max_ratio,
    ):
        return body, None
    return compressed.text, compressed.metadata()


def maybe_compress_read_view(
    body: str,
    *,
    pipeline: str = "",
    kind: str = "",
    context: str = "",
    target_ratio: float = 0.5,
    source_name: str = "",
    source_ext: str = "",
    member_path: str = "",
    allow_code: bool = False,
) -> CompressedText | None:
    """Compress a read_files model view using built-in transforms."""
    if not body.strip():
        return None
    try:
        return _compress_read_text(
            body,
            pipeline=pipeline,
            kind=kind,
            context=context,
            target_ratio=target_ratio,
            source_name=source_name,
            source_ext=source_ext,
            member_path=member_path,
            allow_code=allow_code,
        )
    except Exception as exc:  # noqa: BLE001 - optional dependency boundary
        log.debug("Read compression skipped for %s/%s: %r", pipeline, kind, exc)
        return None


def _compress_query_payload(
    tool_name: str,
    payload: Any,
    *,
    context: str,
) -> CompressedText | None:
    if tool_name == "query_log":
        search_text = _render_query_log_search(payload)
        if search_text:
            return _compress_search_text(search_text, context=context) or _compress_log_text(
                search_text,
                context=context,
            )

    records = _records_from_payload(payload)
    if records:
        return _compress_records(records, context=context)
    return None


def maybe_compress_ingest_aggregate_view(
    body: str,
    *,
    kind: str,
    context: str = "",
) -> tuple[str, dict[str, Any] | None]:
    """Compress long ingest aggregate prompts, never raw indexed chunks."""
    settings = get_settings()
    if not settings.compression_enabled or len(body) < settings.compression_min_chars:
        return body, None
    try:
        compressed = _compress_plain_text(
            body,
            context=context,
            target_ratio=_settings_target_ratio(settings, len(body)),
        )
    except Exception as exc:  # noqa: BLE001 - optional dependency boundary
        log.debug("Aggregate compression skipped for %s: %r", kind, exc)
        return body, None
    if compressed is None or not compressed.text.strip():
        return body, None
    if not _beats_threshold(
        original_chars=len(body),
        compressed_chars=len(compressed.text),
        max_ratio=settings.compression_max_ratio,
    ):
        return body, None
    meta = compressed.metadata()
    meta["aggregate"] = True
    meta["kind"] = kind
    return compressed.text, meta


def maybe_compress_archive_peeks(
    peeks: list[dict[str, Any]],
    *,
    context: str = "",
) -> list[dict[str, Any]]:
    """Compress archive member previews while keeping member_path reopen hints."""
    settings = get_settings()
    if not settings.compression_enabled or not peeks:
        return peeks
    min_chars = min(settings.compression_min_chars, _ARCHIVE_PREVIEW_MIN_CHARS)
    out: list[dict[str, Any]] = []
    for peek in peeks:
        item = dict(peek)
        preview = str(item.get("preview") or "")
        if len(preview) < min_chars:
            out.append(item)
            continue
        path = str(item.get("path") or "")
        kind = str(item.get("kind") or "")
        try:
            compressed = _compress_read_text(
                preview,
                pipeline=kind,
                kind=kind,
                context=context or path,
                target_ratio=_settings_target_ratio(settings, len(preview)),
                source_name=path,
                member_path=path,
                allow_code=True,
            )
        except Exception as exc:  # noqa: BLE001 - optional dependency boundary
            log.debug("Archive peek compression skipped for %s: %r", path, exc)
            out.append(item)
            continue
        if compressed is None or not compressed.text.strip():
            out.append(item)
            continue
        if not _beats_threshold(
            original_chars=len(preview),
            compressed_chars=len(compressed.text),
            max_ratio=settings.compression_max_ratio,
        ):
            out.append(item)
            continue
        item["preview"] = compressed.text
        meta = compressed.metadata()
        meta["reopen"] = {"member_path": path, "compress": False}
        item["compression"] = meta
        out.append(item)
    return out


def _compress_ingest_text(
    body: str,
    *,
    kind: str,
    context: str,
) -> CompressedText | None:
    k = (kind or "").lower()
    if k == "log":
        return _compress_log_text(body, context=context)
    if k == "table":
        return _compress_table_text(body, context=context)

    ext = _route_ext(source_name=context, source_ext="", member_path="")
    route = _read_route(body, pipeline="", kind=k, source_name=context)
    if route == "json":
        return _compress_json_text(body, context=context)
    if route == "table":
        return _compress_table_text(body, context=context)
    if route == "log":
        return _compress_log_text(body, context=context)
    if route == "code":
        return None
    if (
        k in {"pdf", "docx"} or ext in _EXTRACTED_TEXT_EXTS
    ) and len(body) >= _INGEST_TEXT_MIN_CHARS:
        return _compress_plain_text(body, context=context, target_ratio=0.6)
    return None


def _compress_read_text(
    body: str,
    *,
    pipeline: str,
    kind: str,
    context: str,
    target_ratio: float,
    source_name: str = "",
    source_ext: str = "",
    member_path: str = "",
    allow_code: bool = False,
) -> CompressedText | None:
    route = _read_route(
        body,
        pipeline=pipeline,
        kind=kind,
        source_name=source_name,
        source_ext=source_ext,
        member_path=member_path,
    )
    if route == "json":
        compressed = _compress_json_text(body, context=context)
    elif route == "table":
        compressed = _compress_table_text(body, context=context)
    elif route == "search":
        compressed = _compress_search_text(body, context=context)
    elif route == "log":
        compressed = _compress_log_text(body, context=context)
    elif route == "code":
        if not allow_code:
            return None
        compressed = _compress_code_text(body, context=context, target_ratio=target_ratio)
    else:
        compressed = _compress_plain_text(body, context=context, target_ratio=target_ratio)
    if compressed is not None:
        compressed.extra.setdefault("route", route)
    return compressed


def _compress_log_text(text: str, *, context: str) -> CompressedText | None:
    lines = text.splitlines()
    if len(lines) < 8:
        return None
    keep = _select_line_indexes(
        lines,
        context=context,
        max_lines=max(24, min(180, len(lines) // 5)),
        signal_re=_LOG_SIGNAL_RE,
    )
    compressed = _format_selected_lines(
        "compressed log view",
        lines,
        keep,
    )
    if compressed == text or len(compressed) >= len(text):
        return None
    return CompressedText(
        text=compressed,
        strategy="marginalia.log",
        original_chars=len(text),
        compressed_chars=len(compressed),
        extra={
            "line_count_before": len(lines),
            "line_count_after": len(keep),
            "format": "text-log",
            "lossy": True,
            "local": True,
        },
    )

def _compress_search_text(text: str, *, context: str) -> CompressedText | None:
    matches = _parse_search_matches(text)
    if len(matches) < 4:
        return None
    query_terms = _query_terms(context)
    grouped: dict[str, list[tuple[int, str, str]]] = {}
    for idx, path, line_no, body in matches:
        grouped.setdefault(path, []).append((idx, line_no, body))

    selected: set[int] = set()
    for rows in grouped.values():
        rows_sorted = sorted(rows)
        for idx, _, _body in rows_sorted[:2] + rows_sorted[-1:]:
            selected.add(idx)
        if query_terms:
            ranked = sorted(
                rows_sorted,
                key=lambda row: _term_score(row[2], query_terms),
                reverse=True,
            )
            for idx, _, _ in ranked[:3]:
                selected.add(idx)
    if len(selected) > 180:
        selected = set(sorted(selected)[:180])

    parts = [
        "# compressed search view",
        f"# original_matches={len(matches)} kept_matches={len(selected)} files={len(grouped)}",
    ]
    omitted = len(matches) - len(selected)
    if omitted > 0:
        parts.append(f"# omitted_matches={omitted}")
    current = ""
    for idx, path, line_no, body in matches:
        if idx not in selected:
            continue
        if path != current:
            current = path
            parts.append(f"\n## {path}")
        parts.append(f"{line_no}: {body}")
    compressed = "\n".join(parts).strip() + "\n"
    if len(compressed) >= len(text):
        return None
    return CompressedText(
        text=compressed,
        strategy="marginalia.search",
        original_chars=len(text),
        compressed_chars=len(compressed),
        extra={
            "match_count_before": len(matches),
            "match_count_after": len(selected),
            "files_affected": len(grouped),
            "lossy": True,
            "local": True,
        },
    )

def _compress_json_text(text: str, *, context: str) -> CompressedText | None:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        records = _records_from_jsonl(text)
        if records:
            return _compress_records(
                records,
                context=context,
                original_chars=len(text),
                source_format="jsonl",
            )
        return None
    if isinstance(parsed, list):
        records = [dict(item) for item in parsed if isinstance(item, dict)]
        if len(records) == len(parsed) and records:
            return _compress_records(
                records,
                context=context,
                original_chars=len(text),
                source_format="json",
            )
    if isinstance(parsed, dict):
        records = _records_from_payload(parsed)
        if records:
            return _compress_records(
                records,
                context=context,
                original_chars=len(text),
                source_format="json",
            )
    return None


def _compress_table_text(text: str, *, context: str) -> CompressedText | None:
    records = _records_from_table_text(text)
    if not records:
        return None
    return _compress_records(
        records,
        context=context,
        original_chars=len(text),
        source_format="table-text",
        lossy=True,
    )


def _compress_records(
    records: list[dict[str, Any]],
    *,
    context: str,
    original_chars: int | None = None,
    source_format: str = "records",
    lossy: bool = False,
) -> CompressedText | None:
    if len(records) < 2:
        return None
    original = json.dumps(records, ensure_ascii=False, default=str)
    query_terms = _query_terms(context)
    fields = _record_fields(records)
    selected = _select_record_indexes(records, query_terms=query_terms, max_records=24)
    sample = [_compact_record(records[idx], fields=fields) for idx in selected]
    payload = {
        "record_count": len(records),
        "fields": fields,
        "sample_count": len(sample),
        "omitted_records": max(0, len(records) - len(sample)),
        "sample": sample,
    }
    compressed = json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    original_len = original_chars or len(original)
    if len(compressed) >= original_len:
        return None
    if source_format.startswith("json"):
        suffix = "json"
    elif "table" in source_format:
        suffix = "table"
    else:
        suffix = "records"
    return CompressedText(
        text=compressed,
        strategy=f"marginalia.smart_crusher.{suffix}",
        original_chars=original_len,
        compressed_chars=len(compressed),
        extra={
            "record_count": len(records),
            "lossless_only": False,
            "source_format": source_format,
            "sample_count": len(sample),
            "omitted_records": max(0, len(records) - len(sample)),
            "lossy": True if len(sample) < len(records) else lossy,
            "local": True,
        },
    )

def _compress_plain_text(text: str, *, context: str, target_ratio: float) -> CompressedText | None:
    target_chars = max(800, int(len(text) * _clamp_ratio(target_ratio)))
    compressed = _extract_text_view(
        text,
        context=context,
        target_chars=target_chars,
        title="compressed text view",
    )
    if compressed is None or len(compressed) >= len(text):
        return None
    return CompressedText(
        text=compressed,
        strategy="marginalia.text_extract",
        original_chars=len(text),
        compressed_chars=len(compressed),
        extra={
            "compression_ratio": round(len(compressed) / max(1, len(text)), 4),
            "target_ratio": _clamp_ratio(target_ratio),
            "lossy": True,
            "local": True,
        },
    )

def _compress_code_text(text: str, *, context: str, target_ratio: float) -> CompressedText | None:
    lines = text.splitlines()
    keep: set[int] = set(range(min(20, len(lines))))
    keep.update(range(max(0, len(lines) - 12), len(lines)))
    query_terms = _query_terms(context)
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if _CODE_LINE_RE.search(line) or stripped.startswith(("#", "//", "/*", "*")):
            keep.add(idx)
        elif query_terms and _term_score(line, query_terms) > 0:
            _add_window(keep, idx, len(lines), radius=2)
        if len(keep) >= max(40, min(220, len(lines) // 3)):
            break
    compressed = _format_selected_lines("compressed code view", lines, sorted(keep))
    if len(compressed) >= len(text):
        return None
    return CompressedText(
        text=compressed,
        strategy="marginalia.code_aware",
        original_chars=len(text),
        compressed_chars=len(compressed),
        extra={
            "language": None,
            "syntax_valid": None,
            "lossy": True,
            "local": True,
        },
    )

def _read_route(
    text: str,
    *,
    pipeline: str,
    kind: str,
    source_name: str = "",
    source_ext: str = "",
    member_path: str = "",
) -> str:
    p = (pipeline or "").lower()
    k = (kind or "").lower()
    ext = _route_ext(source_name=source_name, source_ext=source_ext, member_path=member_path)
    if p == "spreadsheet" or k == "table" or ext in _TABLE_EXTS:
        return "table"
    if ext in _JSON_EXTS or _looks_json(text) or _looks_jsonl(text):
        return "json"
    if _looks_like_search(text):
        return "search"
    if p == "log" or k == "log" or ext in _LOG_EXTS or _looks_like_log(text):
        return "log"
    if k == "code" or ext in _CODE_EXTS or _looks_like_code(text):
        return "code"
    return "text"


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("entries"), list):
        return [dict(item) for item in payload["entries"] if isinstance(item, dict)]
    if isinstance(payload.get("rows"), list):
        columns = [str(c) for c in payload.get("columns") or []]
        records: list[dict[str, Any]] = []
        for row in payload["rows"]:
            if isinstance(row, dict):
                records.append(dict(row))
            elif isinstance(row, list) and columns:
                records.append({
                    columns[idx] if idx < len(columns) else f"col_{idx + 1}": value
                    for idx, value in enumerate(row)
                })
        return records
    if isinstance(payload.get("results"), list):
        rows: list[dict[str, Any]] = []
        for item in payload["results"]:
            if isinstance(item, dict):
                rows.append(dict(item))
        return rows
    return []


def _records_from_jsonl(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    for line in lines:
        try:
            parsed = json.loads(line)
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, dict):
            return []
        records.append(dict(parsed))
    return records


def _records_from_table_text(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    sheet = ""
    row_no = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# Sheet:"):
            sheet = line.removeprefix("# Sheet:").strip()
            row_no = 0
            continue
        if line.startswith("[...") and "omitted" in line:
            continue
        delimiter = "\t" if "\t" in line else "|" if "|" in line else "," if "," in line else ""
        if not delimiter:
            continue
        cells = [_unescape_table_cell(part.strip()) for part in line.split(delimiter)]
        if len(cells) < 2:
            continue
        row_no += 1
        row: dict[str, Any] = {"row": row_no}
        if sheet:
            row["sheet"] = sheet
        row.update({f"col_{idx}": value for idx, value in enumerate(cells, start=1)})
        records.append(row)
    return records


def _render_query_log_search(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    lines: list[str] = []
    results = payload.get("results")
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict):
                _append_log_matches(lines, result)
    else:
        _append_log_matches(lines, payload)
    return "\n".join(lines)


def _append_log_matches(lines: list[str], result: dict[str, Any]) -> None:
    matches = result.get("matches")
    if not isinstance(matches, list):
        return
    name = str(result.get("display_name") or result.get("entry_id") or "log")
    for idx, item in enumerate(matches, start=1):
        if not isinstance(item, dict):
            continue
        raw_line = item.get("line", idx)
        try:
            line_no = int(raw_line)
        except (TypeError, ValueError):
            line_no = idx
        text = str(item.get("text") or "")
        lines.append(f"{name}:{line_no}:{text}")


def _tool_envelope(tool_name: str, payload: Any, compressed: CompressedText) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "ok": payload.get("ok", True) if isinstance(payload, dict) else True,
        "compressed_for_model": True,
        "tool": tool_name,
        "compression": compressed.metadata(),
        "compressed_text": compressed.text,
    }
    if isinstance(payload, dict):
        for key in (
            "count",
            "total",
            "row_count",
            "match_count",
            "total_matches",
            "truncated",
            "has_more",
            "next_offset",
            "operation",
            "columns",
            "column_fixes",
            "rewritten_sql",
        ):
            if key in payload:
                envelope[key] = payload[key]
    return envelope


def _looks_json(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped.startswith(("[", "{")):
        return False
    try:
        json.loads(text)
    except (TypeError, ValueError):
        return False
    return True


def _looks_like_search(text: str) -> bool:
    return len(_SEARCH_LINE_RE.findall(text[:50_000])) >= 3


def _looks_jsonl(text: str) -> bool:
    return bool(_records_from_jsonl("\n".join(text.splitlines()[:25])))


def _looks_like_log(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 20:
        return False
    levelish = sum(1 for line in lines[:300] if _LOG_SIGNAL_RE.search(line))
    timestamped = sum(
        1
        for line in lines[:300]
        if re.match(r"\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2}\s+\d{1,2}", line)
    )
    return levelish >= 3 or timestamped >= 8


def _looks_like_code(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    hits = sum(1 for line in lines[:200] if _CODE_LINE_RE.search(line))
    brace_lines = sum(
        1 for line in lines[:200] if "{" in line or "}" in line or line.rstrip().endswith(":")
    )
    return hits >= 3 or (hits >= 1 and brace_lines >= 8)


def _route_ext(*, source_name: str, source_ext: str, member_path: str) -> str:
    for candidate in (member_path, source_name, source_ext):
        ext = _suffix(candidate)
        if ext:
            return ext
    return ""


def _suffix(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith(".") and "/" not in raw and "\\" not in raw:
        return raw
    path = PureWindowsPath(raw) if "\\" in raw else PurePosixPath(raw)
    name = path.name
    for suffix in (".jsonl", ".ndjson", ".tar.gz", ".tar.bz2", ".tar.xz"):
        if name.endswith(suffix):
            return suffix
    return PurePosixPath(name).suffix.lower()


def _unescape_table_cell(value: str) -> str:
    return value.replace(r"\|", "|").replace("\\n", " ")


def _settings_target_ratio(settings: Any, original_len: int) -> float:
    if original_len <= 0:
        return 0.5
    try:
        target_chars = int(getattr(settings, "compression_target_chars", 0) or 0)
    except (TypeError, ValueError):
        target_chars = 0
    if target_chars <= 0:
        return 0.6
    return _clamp_ratio(target_chars / original_len)


def _clamp_ratio(value: float) -> float:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        ratio = 0.5
    return min(0.8, max(0.1, ratio))


def _beats_threshold(*, original_chars: int, compressed_chars: int, max_ratio: float) -> bool:
    if original_chars <= 0:
        return False
    return compressed_chars < int(original_chars * max_ratio)



def _query_terms(context: str) -> list[str]:
    terms: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9_./:-]{3,}", context.lower()):
        if raw not in terms:
            terms.append(raw)
        if len(terms) >= 16:
            break
    return terms


def _term_score(text: str, terms: list[str]) -> int:
    if not terms:
        return 0
    haystack = text.lower()
    return sum(1 for term in terms if term in haystack)


def _select_line_indexes(
    lines: list[str],
    *,
    context: str,
    max_lines: int,
    signal_re: re.Pattern[str] | None = None,
) -> list[int]:
    if len(lines) <= max_lines:
        return list(range(len(lines)))
    keep: set[int] = set(range(min(8, len(lines))))
    keep.update(range(max(0, len(lines) - 6), len(lines)))
    terms = _query_terms(context)
    scored: list[tuple[int, int]] = []
    for idx, line in enumerate(lines):
        score = _term_score(line, terms)
        if signal_re is not None and signal_re.search(line):
            score += 2
        if line.lstrip().startswith(("#", "##", "###")):
            score += 1
        if score:
            scored.append((score, idx))
    for _, idx in sorted(scored, reverse=True):
        _add_window(keep, idx, len(lines), radius=1)
        if len(keep) >= max_lines:
            break
    if len(keep) < max_lines:
        stride = max(1, len(lines) // max(1, max_lines - len(keep)))
        for idx in range(0, len(lines), stride):
            keep.add(idx)
            if len(keep) >= max_lines:
                break
    return sorted(keep)


def _add_window(keep: set[int], idx: int, total: int, *, radius: int) -> None:
    for pos in range(max(0, idx - radius), min(total, idx + radius + 1)):
        keep.add(pos)


def _format_selected_lines(title: str, lines: list[str], keep: list[int] | set[int]) -> str:
    ordered = sorted(keep)
    parts = [
        f"# {title}",
        f"# original_lines={len(lines)} kept_lines={len(ordered)} omitted_lines={max(0, len(lines) - len(ordered))}",
    ]
    prev = -1
    for idx in ordered:
        if idx < 0 or idx >= len(lines):
            continue
        if prev >= 0 and idx > prev + 1:
            parts.append(f"# ... omitted {idx - prev - 1} lines ...")
        parts.append(f"{idx + 1}: {lines[idx]}")
        prev = idx
    return "\n".join(parts).strip() + "\n"


def _parse_search_matches(text: str) -> list[tuple[int, str, str, str]]:
    matches: list[tuple[int, str, str, str]] = []
    for idx, line in enumerate(text.splitlines()):
        match = re.match(r"^([^\s:][^:]*):(\d+):(.*)$", line)
        if not match:
            continue
        matches.append((idx, match.group(1), match.group(2), match.group(3).strip()))
    return matches


def _record_fields(records: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, int] = {}
    for record in records:
        for key in record:
            counts[str(key)] = counts.get(str(key), 0) + 1
    return [key for key, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:32]]


def _select_record_indexes(
    records: list[dict[str, Any]],
    *,
    query_terms: list[str],
    max_records: int,
) -> list[int]:
    if len(records) <= max_records:
        return list(range(len(records)))
    keep: set[int] = set(range(min(5, len(records))))
    keep.update(range(max(0, len(records) - 3), len(records)))
    if query_terms:
        scored: list[tuple[int, int]] = []
        for idx, record in enumerate(records):
            text = json.dumps(record, ensure_ascii=False, default=str)
            score = _term_score(text, query_terms)
            if score:
                scored.append((score, idx))
        for _, idx in sorted(scored, reverse=True):
            keep.add(idx)
            if len(keep) >= max_records:
                break
    if len(keep) < max_records:
        stride = max(1, len(records) // max(1, max_records - len(keep)))
        for idx in range(0, len(records), stride):
            keep.add(idx)
            if len(keep) >= max_records:
                break
    return sorted(keep)


def _compact_record(record: dict[str, Any], *, fields: list[str]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for field in fields:
        if field not in record:
            continue
        compact[field] = _compact_value(record[field])
        if len(compact) >= 16:
            break
    return compact


def _compact_value(value: Any) -> Any:
    if isinstance(value, str):
        normalized = " ".join(value.split())
        if len(normalized) > 160:
            return normalized[:120].rstrip() + f" ... <{len(normalized) - 120} chars omitted>"
        return normalized
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > 160:
        return text[:120].rstrip() + f" ... <{len(text) - 120} chars omitted>"
    return value


def _extract_text_view(
    text: str,
    *,
    context: str,
    target_chars: int,
    title: str,
) -> str | None:
    lines = text.splitlines()
    if len(lines) <= 1:
        if len(text) <= target_chars:
            return None
        return f"# {title}\n{text[:target_chars].rstrip()}\n# ... omitted {len(text) - target_chars} chars ...\n"
    avg_line = max(1, len(text) // max(1, len(lines)))
    max_lines = max(16, min(240, target_chars // avg_line))
    keep = _select_line_indexes(lines, context=context, max_lines=max_lines)
    compressed = _format_selected_lines(title, lines, keep)
    if len(compressed) > target_chars:
        tighter = max(8, int(len(keep) * target_chars / max(1, len(compressed))))
        keep = _select_line_indexes(lines, context=context, max_lines=tighter)
        compressed = _format_selected_lines(title, lines, keep)
    return compressed if len(compressed) < len(text) else None

def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)
