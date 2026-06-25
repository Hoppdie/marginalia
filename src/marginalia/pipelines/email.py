"""RFC 822 / MIME email pipeline (.eml).

This is intentionally local and stdlib-only. MarkItDown does not currently
support .eml, and Outlook .msg remains handled by the MarkItDown pipeline.
"""
from __future__ import annotations

from email import policy
from email.message import Message
from email.parser import BytesParser
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable

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

_HEADER_LABELS = (
    ("from", "From"),
    ("to", "To"),
    ("cc", "Cc"),
    ("bcc", "Bcc"),
    ("subject", "Subject"),
    ("date", "Date"),
)


@register_pipeline(
    mimes=("message/rfc822",),
    exts=(".eml",),
    ext_overrides_mime=True,
)
class EmailPipeline(Pipeline):
    name = "email"

    async def run(
        self,
        *,
        ctx: PipelineContext,
        storage: StorageBackend,
    ) -> PipelineResult:
        body, coverage = await self._extract_text_with_coverage(
            storage,
            ctx.storage_key,
        )
        return await index_extracted_text(
            body,
            ctx,
            kind="email",
            coverage=coverage,
        )

    async def read_segment(
        self,
        *,
        file_row: Any,
        args: dict[str, Any],
        storage: StorageBackend,
    ) -> SegmentResult:
        try:
            body = await self._extract_text(storage, file_row.storage_key)
        except Exception as exc:  # noqa: BLE001
            return SegmentResult(error=f"email parse failed: {exc}")
        return TextPipeline()._slice(body=body, args=args, file_row=file_row)

    async def read_segment_from_bytes(
        self,
        body: bytes,
        args: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> SegmentResult:
        try:
            text = _render_email(body)
        except Exception as exc:  # noqa: BLE001
            return SegmentResult(error=f"email parse failed: {exc}")
        return TextPipeline()._slice(body=text, args=args, file_row=None)

    @classmethod
    async def _extract_text(
        cls,
        storage: StorageBackend,
        key: str,
    ) -> str:
        buf = bytearray()
        async for chunk in storage.get(key):
            buf.extend(chunk)
        return _render_email(bytes(buf))

    @classmethod
    async def _extract_text_with_coverage(
        cls,
        storage: StorageBackend,
        key: str,
    ) -> tuple[str, dict[str, Any]]:
        buf = bytearray()
        async for chunk in storage.get(key):
            buf.extend(chunk)
        text = _render_email(bytes(buf))
        full_chars = len(text)
        indexed_partial = full_chars > MAX_INDEX_CHARS
        if indexed_partial:
            text = text[:MAX_INDEX_CHARS] + "\n[...email truncated for indexing...]"
        coverage: dict[str, Any] = {
            "unit": "chars",
            "source_mode": "email_extracted_text",
            "source_format": "eml",
            "total_units": full_chars,
            "indexed_units": min(full_chars, MAX_INDEX_CHARS),
            "total_chars": full_chars,
            "indexed_chars": min(full_chars, MAX_INDEX_CHARS),
            "total_bytes": len(buf),
            "indexed_bytes": len(buf),
            "indexed_partial": indexed_partial,
            "partial_reasons": ["email_index_char_cap"] if indexed_partial else [],
            "max_index_chars": MAX_INDEX_CHARS,
            "chunked": False,
            "chunk_count": 1,
            "text_truncated": indexed_partial,
        }
        return text, coverage


def _render_email(body: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(body)
    parts = ["# Email Message", ""]

    for key, label in _HEADER_LABELS:
        value = _header(message, key)
        if value:
            parts.append(f"**{label}:** {value}")

    content = _body_text(message)
    if content:
        parts.extend(["", "## Content", "", content.strip()])

    attachments = list(_attachments(message))
    if attachments:
        parts.extend(["", "## Attachments", ""])
        parts.extend(f"- {item}" for item in attachments)

    return "\n".join(parts).strip()


def _header(message: Message, key: str) -> str | None:
    value = message.get(key)
    if value is None:
        return None
    return str(value).strip() or None


def _body_text(message: Message) -> str:
    plain = _first_body_part(message, "text/plain")
    if plain:
        return plain

    html = _first_body_part(message, "text/html")
    if html:
        return _html_to_text(html)

    return ""


def _first_body_part(message: Message, content_type: str) -> str | None:
    for part in _iter_leaf_parts(message):
        if part.get_content_type() != content_type:
            continue
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            continue
        text = _part_text(part)
        if text and text.strip():
            return text.strip()
    return None


def _iter_leaf_parts(message: Message) -> Iterable[Message]:
    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            yield part
    else:
        yield message


def _part_text(part: Message) -> str | None:
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset)
        except (LookupError, UnicodeDecodeError):
            return payload.decode("utf-8", errors="replace")

    raw = part.get_payload(decode=False)
    if isinstance(raw, str):
        return raw
    return None


def _attachments(message: Message) -> Iterable[str]:
    for part in _iter_leaf_parts(message):
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if disposition != "attachment" and not filename:
            continue
        name = filename or "unnamed"
        content_type = part.get_content_type()
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            yield f"{name} ({content_type}, {len(payload)} bytes)"
        else:
            yield f"{name} ({content_type})"


def _html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in {"br", "p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._parts.append("\n")
        elif tag == "li":
            self._parts.append("\n- ")

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def text(self) -> str:
        raw = unescape("".join(self._parts))
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line).strip()
