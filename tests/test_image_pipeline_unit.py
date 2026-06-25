from __future__ import annotations

import pytest

from marginalia.pipelines.base import PipelineContext
from marginalia.pipelines.image import ImagePipeline


class _MemoryStorage:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def get(self, key: str):
        assert key == "image-key"
        yield self.body


def _ctx(name: str = "scan.tiff") -> PipelineContext:
    return PipelineContext(
        file_id="file-id",
        storage_key="image-key",
        sha256="sha",
        size_bytes=123,
        mime_type="image/tiff",
        original_ext=".tiff",
        folder_path="/",
        sibling_names=[],
        display_name=name,
    )


@pytest.mark.asyncio
async def test_image_pipeline_without_vision_profile_keeps_file_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marginalia.pipelines.image as mod

    monkeypatch.setattr(mod, "has_vision_profile", lambda: False)

    result = await ImagePipeline().run(
        ctx=_ctx(),
        storage=_MemoryStorage(b"not actually a tiff"),
    )

    assert result.kind == "image"
    assert result.summary == "Image file: scan.tiff"
    assert result.description["coverage"]["source_mode"] == "image_metadata_only"
    assert result.description["coverage"]["reason"] == "vision_profile_missing"


@pytest.mark.asyncio
async def test_image_member_without_vision_profile_returns_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marginalia.pipelines.image as mod

    monkeypatch.setattr(mod, "has_vision_profile", lambda: False)

    result = await ImagePipeline().read_segment_from_bytes(
        b"image bytes",
        {},
        filename="photo.heic",
    )

    assert result.error is None
    assert "photo.heic" in result.text
    assert result.extras["kind"] == "image"
