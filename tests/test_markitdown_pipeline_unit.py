from __future__ import annotations

import pytest

from marginalia.pipelines.base import PipelineContext, PipelineResult
from marginalia.pipelines.markitdown import MarkItDownPipeline


class _MemoryStorage:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def get(self, key: str):
        assert key == "file-key"
        yield self.body


def _ctx(name: str, ext: str) -> PipelineContext:
    return PipelineContext(
        file_id="file-id",
        storage_key="file-key",
        sha256="sha",
        size_bytes=12,
        mime_type=None,
        original_ext=ext,
        folder_path="/",
        sibling_names=[],
        display_name=name,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "ext", "expected_kind"),
    [
        ("rules.xls", ".xls", "table"),
        ("book.epub", ".epub", "ebook"),
        ("message.msg", ".msg", "email"),
    ],
)
async def test_markitdown_pipeline_indexes_supplemental_formats(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    ext: str,
    expected_kind: str,
) -> None:
    captured: dict[str, object] = {}

    def fake_convert(body: bytes, suffix: str) -> str:
        captured["convert_body"] = body
        captured["suffix"] = suffix
        return "alpha\nbeta"

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

    import marginalia.pipelines.markitdown as mod

    monkeypatch.setattr(mod, "_convert_bytes_with_markitdown", fake_convert)
    monkeypatch.setattr(mod, "index_extracted_text", fake_index)

    result = await MarkItDownPipeline().run(
        ctx=_ctx(name, ext),
        storage=_MemoryStorage(b"source bytes"),
    )

    assert result.kind == expected_kind
    assert captured["convert_body"] == b"source bytes"
    assert captured["suffix"] == ext
    assert captured["body"] == "alpha\nbeta"
    assert captured["kind"] == expected_kind
    coverage = captured["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["source_mode"] == "markitdown_extracted_text"
    assert coverage["source_format"] == ext.lstrip(".")


@pytest.mark.asyncio
async def test_markitdown_read_segment_from_bytes_uses_text_slicing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marginalia.pipelines.markitdown as mod

    monkeypatch.setattr(
        mod,
        "_convert_bytes_with_markitdown",
        lambda body, suffix: "first line\nsecond line\nthird line",
    )

    result = await MarkItDownPipeline().read_segment_from_bytes(
        b"source bytes",
        {"line_start": 2, "line_end": 3},
        filename="message.msg",
    )

    assert result.error is None
    assert result.text == "second line\nthird line"
    assert result.extras["line_start"] == 2


@pytest.mark.asyncio
async def test_markitdown_read_segment_uses_original_text_not_index_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marginalia.pipelines.markitdown as mod

    monkeypatch.setattr(
        mod,
        "_convert_bytes_with_markitdown",
        lambda body, suffix: "A" * 125_000 + "\nneedle-after-index-cap",
    )

    result = await MarkItDownPipeline().read_segment(
        file_row=type(
            "FileRow",
            (),
            {
                "storage_key": "file-key",
                "original_ext": ".msg",
                "description": None,
            },
        )(),
        args={"pattern": "needle-after-index-cap", "context_lines": 0},
        storage=_MemoryStorage(b"source bytes"),
    )

    assert result.error is None
    assert "needle-after-index-cap" in result.text
