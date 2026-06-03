from __future__ import annotations

import json

from marginalia.agent.read_compression import (
    CompressionSettings,
    compress_read_text,
)
from marginalia.pipelines import resolve_pipeline


def _cfg() -> CompressionSettings:
    return CompressionSettings(
        enabled=True,
        min_chars=500,
        target_chars=1_600,
        context_chars=120,
    )


def test_pdf_page_sampling_preserves_page_markers_and_reopen_args() -> None:
    pages = []
    for page in range(1, 12):
        marker = " needle evidence" if page == 7 else ""
        body = (f"Page {page} background{marker}. " * 80).strip()
        pages.append(f"[Page {page}]\n{body}")
    text = "\n\n".join(pages)

    result = compress_read_text(
        text,
        entry_id="entry-pdf",
        args={"max_chars": 16_000},
        pipeline="pdf",
        query="needle evidence",
        settings=_cfg(),
    )

    assert result.compressed is True
    assert result.strategy == "pdf_text"
    assert "[Page 1]" in result.text
    assert "[Page 7]" in result.text
    assert "omitted pages" in result.text
    assert result.omitted
    assert all(item["read_files_args"]["compress"] is False for item in result.omitted)
    assert result.metadata()["lossy"] is True


def test_pattern_reads_are_not_compressed() -> None:
    text = "needle\n" + ("large context\n" * 1_000)

    result = compress_read_text(
        text,
        entry_id="entry-pattern",
        args={"pattern": "needle"},
        extras={"hits": [{"line": 1}]},
        pipeline="text",
        query="needle",
        settings=_cfg(),
    )

    assert result.compressed is False
    assert result.text == text


def test_context_chars_clips_long_relevant_text_units() -> None:
    target_paragraph = (
        ("background prefix " * 80)
        + "target signal"
        + (" background suffix" * 80)
    )
    text = "\n\n".join([
        "opening background " * 80,
        "middle background " * 80,
        target_paragraph,
        "closing background " * 80,
    ])

    small_context = compress_read_text(
        text,
        entry_id="entry-text",
        args={"max_chars": 16_000},
        pipeline="text",
        query="target signal",
        settings=CompressionSettings(
            enabled=True,
            min_chars=500,
            target_chars=2_400,
            context_chars=80,
        ),
    )
    large_context = compress_read_text(
        text,
        entry_id="entry-text",
        args={"max_chars": 16_000},
        pipeline="text",
        query="target signal",
        settings=CompressionSettings(
            enabled=True,
            min_chars=500,
            target_chars=2_400,
            context_chars=360,
        ),
    )

    assert small_context.compressed is True
    assert large_context.compressed is True
    assert "target signal" in small_context.text
    assert "omitted chars" in small_context.text
    assert len(small_context.text) < len(large_context.text)


def test_json_list_sampling_marks_omitted_items() -> None:
    items = [
        {
            "id": idx,
            "name": f"item-{idx}",
            "payload": "target signal" if idx == 25 else "background " * 20,
        }
        for idx in range(60)
    ]
    text = json.dumps(items, ensure_ascii=False, indent=2)

    result = compress_read_text(
        text,
        entry_id="entry-json",
        args={"max_chars": 16_000},
        pipeline="text",
        query="target signal",
        settings=_cfg(),
    )

    assert result.compressed is True
    assert result.strategy == "json"
    rendered = json.loads(result.text)
    assert any("_marginalia_omitted_items" in item for item in rendered)
    assert any(item.get("id") == 25 for item in rendered if isinstance(item, dict))
    assert result.omitted
    assert result.omitted[0]["read_files_args"]["compress"] is False


def test_log_line_sampling_preserves_relevant_lines_and_reopen_args() -> None:
    lines = []
    for idx in range(1, 220):
        level = "ERROR" if idx in {80, 151} else "INFO"
        marker = " target failure" if idx == 151 else ""
        lines.append(f"2026-06-03T10:{idx % 60:02d}:00Z {level} event {idx}{marker}")
    text = "\n".join(lines)

    result = compress_read_text(
        text,
        entry_id="entry-log",
        args={"max_chars": 16_000},
        pipeline="log",
        query="target failure",
        settings=_cfg(),
    )

    assert result.compressed is True
    assert result.strategy == "log"
    assert "target failure" in result.text
    assert "omitted lines" in result.text
    assert result.omitted
    first_args = result.omitted[0]["read_files_args"]
    assert "line_start" in first_args
    assert first_args["compress"] is False


def test_code_line_sampling_keeps_definitions() -> None:
    lines = [
        "import os",
        "import sys",
        "",
        "class Worker:",
        "    def run(self):",
        "        return 'ok'",
    ]
    for idx in range(180):
        lines.append(f"VALUE_{idx} = {idx}")
    lines.extend([
        "def target_function():",
        "    important_value = 'target signal'",
        "    return important_value",
    ])
    for idx in range(180, 260):
        lines.append(f"TAIL_{idx} = {idx}")
    text = "\n".join(lines)

    result = compress_read_text(
        text,
        entry_id="entry-code",
        args={"max_chars": 16_000},
        pipeline="text",
        kind="text",
        query="target signal",
        settings=_cfg(),
    )

    assert result.compressed is True
    assert result.strategy == "code"
    assert "def target_function" in result.text
    assert "omitted lines" in result.text
    assert result.omitted


def test_text_pipeline_routes_json_and_code_extensions() -> None:
    assert resolve_pipeline("application/json", ".json", filename="data.json").name == "text"
    assert resolve_pipeline(None, ".py", filename="worker.py").name == "text"
