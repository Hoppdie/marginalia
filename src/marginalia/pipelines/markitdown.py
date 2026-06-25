"""MarkItDown-backed extraction pipeline.

Formats with structure-aware local pipelines (PDF, DOCX, PPTX, XLSX, ZIP,
and images) keep those implementations. MarkItDown handles formats where the
best backend representation is extracted Markdown/text, such as legacy XLS,
EPUB, and Outlook MSG messages.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

from marginalia.pipelines._text_indexer import index_extracted_text
from marginalia.pipelines.base import (
    Pipeline,
    PipelineContext,
    PipelineResult,
    SegmentResult,
)
from marginalia.pipelines.registry import register_pipeline
from marginalia.pipelines.text import TextPipeline
from marginalia.storage.base import StorageBackend

MAX_INDEX_CHARS = 120_000

_TABLE_EXTS = {".xls"}
_EMAIL_EXTS = {".msg"}
_EBOOK_EXTS = {".epub"}


@register_pipeline(
    mimes=(
        "application/vnd.ms-excel",
        "application/epub+zip",
        "application/vnd.ms-outlook",
    ),
    exts=(".xls", ".epub", ".msg"),
    ext_overrides_mime=True,
)
class MarkItDownPipeline(Pipeline):
    name = "markitdown"

    async def run(
        self,
        *,
        ctx: PipelineContext,
        storage: StorageBackend,
    ) -> PipelineResult:
        suffix = _suffix_from_ctx(ctx)
        body, coverage = await self._extract_text_with_coverage(
            storage,
            ctx.storage_key,
            suffix=suffix,
        )
        return await index_extracted_text(
            body,
            ctx,
            kind=_kind_for_suffix(suffix),
            coverage=coverage,
        )

    async def read_segment(
        self,
        *,
        file_row: Any,
        args: dict[str, Any],
        storage: StorageBackend,
    ) -> SegmentResult:
        suffix = _suffix_from_file_row(file_row)
        try:
            body = await self._extract_text(storage, file_row.storage_key, suffix=suffix)
        except Exception as exc:  # noqa: BLE001
            return SegmentResult(error=f"markitdown parse failed: {exc}")
        return TextPipeline()._slice(body=body, args=args, file_row=file_row)

    async def read_segment_from_bytes(
        self,
        body: bytes,
        args: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> SegmentResult:
        suffix = _suffix_from_filename(filename) or ".bin"
        try:
            text = await asyncio.to_thread(_convert_bytes_with_markitdown, body, suffix)
        except Exception as exc:  # noqa: BLE001
            return SegmentResult(error=f"markitdown parse failed: {exc}")
        return TextPipeline()._slice(body=text, args=args, file_row=None)

    @classmethod
    async def _extract_text(
        cls,
        storage: StorageBackend,
        key: str,
        *,
        suffix: str,
    ) -> str:
        buf = bytearray()
        async for chunk in storage.get(key):
            buf.extend(chunk)
        return await asyncio.to_thread(
            _convert_bytes_with_markitdown,
            bytes(buf),
            suffix,
        )

    @classmethod
    async def _extract_text_with_coverage(
        cls,
        storage: StorageBackend,
        key: str,
        *,
        suffix: str,
    ) -> tuple[str, dict[str, Any]]:
        buf = bytearray()
        async for chunk in storage.get(key):
            buf.extend(chunk)
        text = await asyncio.to_thread(
            _convert_bytes_with_markitdown,
            bytes(buf),
            suffix,
        )
        full_chars = len(text)
        indexed_partial = full_chars > MAX_INDEX_CHARS
        if indexed_partial:
            text = text[:MAX_INDEX_CHARS] + "\n[...document truncated for indexing...]"
        coverage: dict[str, Any] = {
            "unit": "chars",
            "source_mode": "markitdown_extracted_text",
            "source_format": suffix.lstrip(".").lower(),
            "total_units": full_chars,
            "indexed_units": min(full_chars, MAX_INDEX_CHARS),
            "total_chars": full_chars,
            "indexed_chars": min(full_chars, MAX_INDEX_CHARS),
            "total_bytes": len(buf),
            "indexed_bytes": len(buf),
            "indexed_partial": indexed_partial,
            "partial_reasons": ["markitdown_index_char_cap"] if indexed_partial else [],
            "max_index_chars": MAX_INDEX_CHARS,
            "chunked": False,
            "chunk_count": 1,
            "text_truncated": indexed_partial,
        }
        return text, coverage


def _convert_bytes_with_markitdown(body: bytes, suffix: str) -> str:
    try:
        from markitdown import MarkItDown  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "markitdown pipeline needs project dependencies; run `uv sync`"
        ) from exc

    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            path = handle.name
            handle.write(body)
        result = MarkItDown().convert(path)
        return (getattr(result, "text_content", "") or "").strip()
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _kind_for_suffix(suffix: str) -> str:
    ext = suffix.lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    if ext in _TABLE_EXTS:
        return "table"
    if ext in _EMAIL_EXTS:
        return "email"
    if ext in _EBOOK_EXTS:
        return "ebook"
    return "text"


def _suffix_from_ctx(ctx: PipelineContext) -> str:
    return _suffix_from_filename(ctx.display_name) or ctx.original_ext or ".bin"


def _suffix_from_file_row(file_row: Any) -> str:
    return _suffix_from_filename(getattr(file_row, "display_name", None)) or (
        getattr(file_row, "original_ext", None) or ".bin"
    )


def _suffix_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    suffix = os.path.splitext(filename)[1].lower()
    return suffix or None
