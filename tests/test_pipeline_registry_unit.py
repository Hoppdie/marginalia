from __future__ import annotations

from marginalia.pipelines import resolve_pipeline


def test_image_pipeline_routes_only_supported_raster_mimes() -> None:
    assert resolve_pipeline("image/png", ".png", filename="pixel.png").name == "image"
    assert resolve_pipeline("image/jpeg", ".jpg", filename="photo.jpg").name == "image"
    assert resolve_pipeline("image/svg+xml", ".svg", filename="icon.svg") is None
