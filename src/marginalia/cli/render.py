"""Minimal markdown→ANSI renderer in Claude Code's spirit.

Rules:
  - Headings (#  ##  ###) → reverse video on the title text only,
    flushed left, single line. No big bordered blocks.
  - Code fences ``` → indented block, dim color, no boxes.
  - Inline `code` → cyan.
  - **bold** / __bold__ → bold. *italic* / _italic_ → italic.
  - Lists ('- ' / '* ' / '1. ') → keep as-is, lightly indent nested.
  - Blockquotes (> ) → vertical bar prefix in dim.
  - Footnote refs `[^a]` → kept literal (Marginalia agent uses these).
  - Links `[text](url)` → text underlined; url shown afterwards in dim.
  - Tables → aligned column output with dim borders.
  - Hr (---) → dim '────'.

Designed to be readable in monospace terminals. No third-party dependency.

Spinner / progress indicators inspired by claw-code's `render.rs`.
"""
from __future__ import annotations

import itertools
import os
import re
import sys
import threading
import time
from contextlib import contextmanager

# ---- ANSI codes ------------------------------------------------------------

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
UNDER = "\x1b[4m"
REV = "\x1b[7m"

CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
BLUE = "\x1b[34m"
YELLOW = "\x1b[33m"
DIM_GREY = "\x1b[90m"

CLEAR_LINE = "\x1b[2K"
CR = "\r"


def _osc8(text: str, url: str) -> str:
    """OSC-8 hyperlink. Falls back to plain underline + dim url when colour
    is unsupported."""
    if not _COLOR:
        return text + " (" + url + ")"
    return f"\x1b]8;;{url}\x1b\\{UNDER}{text}{RESET}\x1b]8;;\x1b\\"


def _enable_windows_vt() -> bool:
    """Flip on ENABLE_VIRTUAL_TERMINAL_PROCESSING for both stdout and stderr
    consoles. No-op (returns True) on non-Windows. Returns False if the call
    fails — caller falls back to plain text."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # GetStdHandle: -11 = STD_OUTPUT_HANDLE, -12 = STD_ERROR_HANDLE
        for std_id in (-11, -12):
            handle = kernel32.GetStdHandle(std_id)
            if handle in (0, -1):
                continue
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except Exception:
        return False


def _supports_color() -> bool:
    if "NO_COLOR" in os.environ:
        return False
    if not sys.stdout.isatty():
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    if not _enable_windows_vt():
        return False
    return True


_COLOR = _supports_color()


def _wrap(s: str, *codes: str) -> str:
    if not _COLOR or not s:
        return s
    return "".join(codes) + s + RESET


# ---- inline rendering ------------------------------------------------------

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC = re.compile(r"(?<![*\w])\*([^*]+)\*(?!\w)|(?<![_\w])_([^_]+)_(?!\w)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_FOOTNOTE_REF = re.compile(r"\[\^([^\]]+)\]")
_FOOTNOTE_DEF = re.compile(r"^(\[\^[^\]]+\]:)\s*(.*)$")


def _render_inline(text: str) -> str:
    # link first — later passes inject ANSI '[' bytes that confuse _LINK.
    text = _LINK.sub(lambda m: _osc8(m.group(1), m.group(2)), text)
    # footnote refs render as a small blue-bold tag, distinct from a bare
    # `[a]` literal so the eye picks them up as citation markers.
    text = _FOOTNOTE_REF.sub(
        lambda m: _wrap(f"[^{m.group(1)}]", BLUE, BOLD), text,
    )
    text = _INLINE_CODE.sub(lambda m: _wrap(m.group(1), BLUE), text)
    text = _BOLD.sub(lambda m: _wrap(m.group(1) or m.group(2), BOLD), text)
    text = _ITALIC.sub(lambda m: _wrap(m.group(1) or m.group(2), ITALIC), text)
    return text


# ---- block rendering ------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_HR = re.compile(r"^\s*([-*_])\s*\1\s*\1\s*$")
_FENCE = re.compile(r"^\s*```([^\s`]*)\s*$")
_BLOCKQUOTE = re.compile(r"^>\s?(.*)$")


def render_markdown(md: str) -> str:
    """Return an ANSI-coloured rendering of `md`. Always returns a string;
    when colour is unsupported the markup is stripped to plain text.

    Blocks (heading / paragraph / code-fence / blockquote / list / table /
    hr) are separated by a single blank line — runs of list items or
    blockquote lines stay tight."""
    blocks: list[list[str]] = []
    cur: list[str] = []
    last_kind: str | None = None

    def _flush(kind: str | None) -> None:
        nonlocal cur, last_kind
        if cur:
            blocks.append(cur)
            cur = []
        last_kind = kind

    in_fence = False
    table_buf: list[str] = []

    for raw in md.splitlines():
        if in_fence:
            if _FENCE.match(raw):
                in_fence = False
                continue
            cur.append(_wrap("    " + raw, DIM_GREY))
            continue

        m = _FENCE.match(raw)
        if m:
            if last_kind != "code":
                _flush("code")
            in_fence = True
            lang = m.group(1) or None
            if lang:
                cur.append(_wrap(f"    {lang}", DIM))
            last_kind = "code"
            continue

        if _looks_like_table_row(raw):
            table_buf.append(raw)
            continue
        elif table_buf:
            _flush("table")
            cur.append(render_table(table_buf))
            table_buf.clear()
            _flush("table")

        m = _HEADING.match(raw)
        if m:
            _flush("heading")
            level = len(m.group(1))
            title = m.group(2)
            if level == 1:
                cur.append(_wrap(title, BOLD, ITALIC, UNDER))
            else:
                cur.append(_wrap(title, BOLD))
            _flush("heading")
            continue

        m = _BLOCKQUOTE.match(raw)
        if m:
            if last_kind != "quote":
                _flush("quote")
            inner = _render_inline(m.group(1))
            cur.append(_wrap("│", DIM_GREY) + " " + _wrap(inner, ITALIC))
            last_kind = "quote"
            continue

        m = _FOOTNOTE_DEF.match(raw)
        if m:
            if last_kind != "footnote":
                _flush("footnote")
            marker = _wrap(m.group(1), BLUE, BOLD)
            body = _wrap(_render_inline(m.group(2)), DIM)
            cur.append(marker + " " + body)
            last_kind = "footnote"
            continue

        if _HR.match(raw):
            _flush("hr")
            cur.append("---")
            _flush("hr")
            continue

        if not raw.strip():
            _flush(None)
            continue

        # list item: keep adjacent items in the same block (no gap between).
        stripped = raw.lstrip()
        is_list = (
            stripped.startswith(("- ", "* ", "+ "))
            or bool(re.match(r"^\d+\.\s", stripped))
        )
        if is_list:
            if last_kind != "list":
                _flush("list")
            cur.append(_render_inline(raw))
            last_kind = "list"
            continue

        # paragraph: flush if previous was a different block.
        if last_kind not in (None, "para"):
            _flush("para")
        cur.append(_render_inline(raw))
        last_kind = "para"

    if table_buf:
        _flush("table")
        cur.append(render_table(table_buf))
        table_buf.clear()
    _flush(None)

    return "\n\n".join("\n".join(b) for b in blocks)


def print_markdown(md: str) -> None:
    print(render_markdown(md))


# ---- table rendering ------------------------------------------------------

_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*[:\- ]+\s*(\|\s*[:\- ]+\s*)*\|?\s*$")


def _looks_like_table_row(line: str) -> bool:
    """A markdown table row starts and ends with `|`."""
    return bool(_TABLE_ROW_RE.match(line))


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def render_table(rows: list[str]) -> str:
    """Render markdown table lines with `|` column separators and a `-`
    header rule. No outer frame — matches Claude Code's `applyMarkdown`."""
    if not rows:
        return ""
    parsed: list[list[str]] = []
    has_header = False
    for i, line in enumerate(rows):
        if i == 1 and _TABLE_SEPARATOR_RE.match(line):
            has_header = True
            continue
        parsed.append(_split_row(line))
    if not parsed:
        return ""
    n_cols = max(len(r) for r in parsed)
    for r in parsed:
        while len(r) < n_cols:
            r.append("")
    widths = [
        max(_visible_len(r[c]) for r in parsed)
        for c in range(n_cols)
    ]

    def _fmt_row(cells: list[str], *, bold: bool = False) -> str:
        formatted: list[str] = []
        for c, cell in enumerate(cells):
            pad = widths[c] - _visible_len(cell)
            inner = cell + " " * pad
            if bold:
                inner = _wrap(inner, BOLD)
            formatted.append(" " + inner + " ")
        return "|" + "|".join(formatted) + "|"

    out_lines: list[str] = []
    for i, row in enumerate(parsed):
        out_lines.append(_fmt_row(row, bold=(has_header and i == 0)))
        if has_header and i == 0:
            sep_cells = ["-" * (w + 2) for w in widths]
            out_lines.append("|" + "|".join(sep_cells) + "|")
    return "\n".join(out_lines)


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(s: str) -> int:
    """Length of `s` excluding ANSI escape sequences."""
    return len(_ANSI_ESCAPE_RE.sub("", s))


# ---- spinner --------------------------------------------------------------

SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def short_duration(seconds: float) -> str:
    """Short human duration: `Nms` / `X.Ys` / `XmYs` / `XhYm`."""
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = int(seconds - m * 60)
    if m < 60:
        return f"{m}m {s}s"
    h = m // 60
    m = m % 60
    return f"{h}h {m}m"


class Spinner:
    """Animates one indented step line, kb-lite style.

    Render pattern:
      while running:   `  ⠋ <label>  3.1s`        (BLUE spinner + DIM elapsed)
      after finish():  `  -> <label>  3.1s`       (whole line DIM, kept in scrollback)
      after fail():    `  ✗  <label>  3.1s`        (RED marker, message kept)

    A single fixed indent (default 2 spaces) is used for every step so the
    column stays aligned regardless of phase. `update()` swaps the label
    in place; `finish()` / `fail()` commit the line and emit a newline so
    the next step starts below.

    No-op when stdout is not a TTY so piped output stays clean.
    """

    def __init__(self, label: str = "", indent: int = 2) -> None:
        self._label = label
        self._indent = " " * indent
        self._frames = itertools.cycle(SPINNER_FRAMES)
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._t0 = time.monotonic()
        self._committed = False
        self._enabled = (
            sys.stdout.isatty()
            and "NO_COLOR" not in os.environ
            and os.environ.get("TERM", "") != "dumb"
        )

    def update(self, label: str) -> None:
        self._label = label

    def start(self) -> "Spinner":
        if not self._enabled or self._thread is not None:
            return self
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while self._stop_event is not None and not self._stop_event.is_set():
            frame = next(self._frames)
            elapsed = short_duration(time.monotonic() - self._t0)
            sys.stdout.write(
                f"{CR}{CLEAR_LINE}{self._indent}{BLUE}{frame}{RESET} "
                f"{self._label}  {DIM}{elapsed}{RESET}"
            )
            sys.stdout.flush()
            time.sleep(0.08)

    def _stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
        self._stop_event = None
        self._thread = None

    def _commit(self, marker: str, color: str | None, label: str | None) -> None:
        if self._committed:
            return
        self._committed = True
        self._stop()
        if not self._enabled:
            return
        msg = label if label is not None else self._label
        elapsed = short_duration(time.monotonic() - self._t0)
        if color is None:
            # kb-lite-style: whole line dimmed, no marker. The committed
            # line is just the same indented label, dim, with the final
            # elapsed appended — leaves a clean trail in scrollback.
            line = f"{DIM}{self._indent}{msg}  {elapsed}{RESET}"
        else:
            line = (
                f"{self._indent}{color}{marker}{RESET} {msg}  "
                f"{DIM}{elapsed}{RESET}"
            )
        sys.stdout.write(f"{CR}{CLEAR_LINE}{line}\n")
        sys.stdout.flush()

    def finish(self, label: str | None = None) -> None:
        self._commit("✓", GREEN, label)

    def fail(self, label: str | None = None) -> None:
        self._commit("✗", RED, label)

    def __enter__(self) -> "Spinner":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.fail(str(exc) if exc else None)
        else:
            self.finish()


@contextmanager
def spinner(label: str):
    """Functional shortcut: `with spinner('working...'): ...`."""
    sp = Spinner(label).start()
    try:
        yield sp
        sp.finish()
    except Exception as exc:
        sp.fail(str(exc))
        raise
