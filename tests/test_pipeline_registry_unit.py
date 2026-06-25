from __future__ import annotations

from marginalia.pipelines import resolve_pipeline
from marginalia.pipelines.archive import _resolve_inner


def test_image_pipeline_routes_supported_image_mimes_and_extensions() -> None:
    assert resolve_pipeline("image/png", ".png", filename="pixel.png").name == "image"
    assert resolve_pipeline("image/jpeg", ".jpg", filename="photo.jpg").name == "image"
    assert resolve_pipeline("image/tiff", ".tiff", filename="scan.tiff").name == "image"
    assert resolve_pipeline("image/heic", ".heic", filename="photo.heic").name == "image"
    assert (
        resolve_pipeline("application/octet-stream", ".heif", filename="photo.heif").name
        == "image"
    )


def test_svg_routes_to_text_pipeline() -> None:
    assert resolve_pipeline("image/svg+xml", ".svg", filename="icon.svg").name == "text"
    assert resolve_pipeline("", ".svg", filename="diagram.svg").name == "text"


def test_archive_svg_member_routes_to_text_pipeline() -> None:
    assert _resolve_inner("diagrams/path-only.svg").name == "text"


def test_markitdown_pipeline_routes_supplemental_formats() -> None:
    assert (
        resolve_pipeline("application/vnd.ms-excel", ".xls", filename="rules.xls").name
        == "markitdown"
    )
    assert (
        resolve_pipeline("application/epub+zip", ".epub", filename="book.epub").name
        == "markitdown"
    )
    assert (
        resolve_pipeline("application/vnd.ms-outlook", ".msg", filename="mail.msg").name
        == "markitdown"
    )


def test_email_pipeline_routes_eml() -> None:
    assert (
        resolve_pipeline("message/rfc822", ".eml", filename="thread.eml").name
        == "email"
    )


def test_legacy_word_and_powerpoint_are_not_registered_as_supported() -> None:
    assert resolve_pipeline("application/msword", ".doc", filename="legacy.doc") is None
    assert resolve_pipeline("application/vnd.ms-powerpoint", ".ppt", filename="legacy.ppt") is None


def test_archive_supplemental_members_route_to_leaf_pipelines() -> None:
    assert _resolve_inner("legacy/rules.xls").name == "markitdown"
    assert _resolve_inner("books/spec.epub").name == "markitdown"
    assert _resolve_inner("mail/thread.eml").name == "email"
    assert _resolve_inner("mail/message.msg").name == "markitdown"
