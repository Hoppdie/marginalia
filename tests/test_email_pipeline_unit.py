from __future__ import annotations

import pytest

from marginalia.pipelines.base import PipelineContext, PipelineResult
from marginalia.pipelines.email import EmailPipeline


class _MemoryStorage:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def get(self, key: str):
        assert key == "file-key"
        yield self.body


def _ctx() -> PipelineContext:
    return PipelineContext(
        file_id="file-id",
        storage_key="file-key",
        sha256="sha",
        size_bytes=12,
        mime_type="message/rfc822",
        original_ext=".eml",
        folder_path="/",
        sibling_names=[],
        display_name="thread.eml",
    )


def _sample_eml() -> bytes:
    return b"\r\n".join([
        b"From: sender@example.com",
        b"To: recipient@example.com",
        b"Subject: Test EML",
        b"Date: Thu, 25 Jun 2026 10:00:00 +0800",
        b"MIME-Version: 1.0",
        b"Content-Type: multipart/mixed; boundary=boundary42",
        b"",
        b"--boundary42",
        b"Content-Type: text/plain; charset=utf-8",
        b"",
        b"Hello from an EML body.",
        b"--boundary42",
        b"Content-Type: text/plain; name=notes.txt",
        b"Content-Disposition: attachment; filename=notes.txt",
        b"",
        b"attachment text",
        b"--boundary42--",
        b"",
    ])


@pytest.mark.asyncio
async def test_email_pipeline_indexes_headers_body_and_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_index(body, ctx, kind, *, coverage=None):
        captured["body"] = body
        captured["kind"] = kind
        captured["coverage"] = coverage
        return PipelineResult(
            summary="ok",
            description={"sections": [], "coverage": coverage},
            kind=kind,
            extra=None,
            entry_extra=None,
            entry_catalog_path=None,
            entry_tags=[],
        )

    import marginalia.pipelines.email as mod

    monkeypatch.setattr(mod, "index_extracted_text", fake_index)

    result = await EmailPipeline().run(
        ctx=_ctx(),
        storage=_MemoryStorage(_sample_eml()),
    )

    assert result.kind == "email"
    body = captured["body"]
    assert isinstance(body, str)
    assert "# Email Message" in body
    assert "**From:** sender@example.com" in body
    assert "**To:** recipient@example.com" in body
    assert "**Subject:** Test EML" in body
    assert "Hello from an EML body." in body
    assert "notes.txt (text/plain" in body
    assert captured["kind"] == "email"
    coverage = captured["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["source_mode"] == "email_extracted_text"
    assert coverage["source_format"] == "eml"


@pytest.mark.asyncio
async def test_email_read_segment_from_bytes_uses_text_slicing() -> None:
    result = await EmailPipeline().read_segment_from_bytes(
        _sample_eml(),
        {"pattern": "EML body", "context_lines": 1},
        filename="thread.eml",
    )

    assert result.error is None
    assert "Hello from an EML body." in result.text
    assert result.extras["total_matches"] == 1


@pytest.mark.asyncio
async def test_email_pipeline_falls_back_to_html_body() -> None:
    body = b"\r\n".join([
        b"From: sender@example.com",
        b"To: recipient@example.com",
        b"Subject: HTML EML",
        b"MIME-Version: 1.0",
        b"Content-Type: text/html; charset=utf-8",
        b"",
        b"<html><body><h1>Hello</h1><p>HTML body</p></body></html>",
    ])

    result = await EmailPipeline().read_segment_from_bytes(
        body,
        {"max_chars": 1000},
        filename="thread.eml",
    )

    assert result.error is None
    assert "Hello" in result.text
    assert "HTML body" in result.text


@pytest.mark.asyncio
async def test_email_read_segment_uses_original_text_not_index_cap() -> None:
    long_body = ("A" * 125_000 + "\nneedle-after-index-cap").encode("utf-8")
    message = b"\r\n".join([
        b"From: sender@example.com",
        b"To: recipient@example.com",
        b"Subject: Long EML",
        b"MIME-Version: 1.0",
        b"Content-Type: text/plain; charset=utf-8",
        b"",
        long_body,
    ])

    result = await EmailPipeline().read_segment(
        file_row=type("FileRow", (), {"storage_key": "file-key", "description": None})(),
        args={"pattern": "needle-after-index-cap", "context_lines": 0},
        storage=_MemoryStorage(message),
    )

    assert result.error is None
    assert "needle-after-index-cap" in result.text
