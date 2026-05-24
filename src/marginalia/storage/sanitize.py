"""Portable filename sanitization for the mirror backend.

Goal: a vault produced on Linux must rsync cleanly to Windows / macOS.
We sanitize against the strictest of the three (Windows) so the same
file paths are valid everywhere — Linux users lose a few special
characters they could otherwise use, but the vault stays portable
which is the whole point of mirror mode.

Strategy:
  - Replace illegal characters with `_`.
  - Strip trailing spaces and dots (Windows silently drops these).
  - Append `_` to reserved Windows device names (CON, PRN, ...) so
    `CON.txt` becomes `CON_.txt`. Detection is case-insensitive and
    applies to the basename without extension.
  - Truncate to 200 UTF-8 bytes; oversize names get a 6-char hex hash
    suffix (split before extension) to avoid silent collision.
  - Empty result becomes `unnamed`.
"""
from __future__ import annotations

import hashlib
import re

ILLEGAL_CHARS = '<>:"/\\|?*'
ILLEGAL_CONTROL = "".join(chr(i) for i in range(0, 32))
_TRANSLATION = str.maketrans(
    {ch: "_" for ch in ILLEGAL_CHARS + ILLEGAL_CONTROL}
)

# Windows reserved device names. Match basename-without-extension,
# case-insensitive.
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MAX_UTF8_BYTES = 200
HASH_SUFFIX_LEN = 6


def sanitize_name(name: str) -> str:
    """Return a portable filename derived from `name`."""
    if not name:
        return "unnamed"
    s = name.translate(_TRANSLATION)
    s = s.rstrip(" .")
    if not s:
        return "unnamed"

    # Reserved device names, basename without ext.
    base, dot, ext = s.partition(".")
    if base.upper() in RESERVED_NAMES:
        s = f"{base}_{dot}{ext}" if dot else f"{base}_"

    if len(s.encode("utf-8")) <= MAX_UTF8_BYTES:
        return s

    # Truncate keeping the extension if any. The hash makes truncated
    # names collision-resistant.
    stem, dot, ext = s.rpartition(".")
    if not dot:
        stem, ext = s, ""
    else:
        ext = "." + ext if ext else ""
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()[:HASH_SUFFIX_LEN]
    suffix = f"-{h}{ext}"
    suffix_bytes = len(suffix.encode("utf-8"))
    budget = MAX_UTF8_BYTES - suffix_bytes
    if budget <= 0:
        return f"long-{h}{ext}"[:MAX_UTF8_BYTES]
    truncated = _truncate_utf8(stem, budget)
    return truncated + suffix


def sanitize_folder(folder_path: str) -> str:
    """Sanitize a folder_path like '/research/llm' into a posix-style
    relative path 'research/llm', with each segment passed through
    `sanitize_name`. Leading/trailing slashes are stripped; empty input
    becomes ''.

    Returns '' for the root folder (which the caller then writes
    directly under the vault root with no subdir).
    """
    if not folder_path:
        return ""
    parts = [p for p in folder_path.replace("\\", "/").split("/") if p]
    safe = [sanitize_name(p) for p in parts]
    return "/".join(safe)


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """Truncate `s` so its UTF-8 encoding is at most `max_bytes`. Slices
    on character boundaries; never produces a malformed byte sequence."""
    if max_bytes <= 0:
        return ""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    cut = encoded[:max_bytes]
    return cut.decode("utf-8", errors="ignore")


_SAFE_RE = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]*$")


def is_portable(name: str) -> bool:
    """True if `name` is already a valid portable filename. Used by
    tests + migrate to verify we're not handing the OS something it'll
    reject."""
    if not name or len(name.encode("utf-8")) > MAX_UTF8_BYTES:
        return False
    if name.rstrip(" .") != name:
        return False
    base, dot, _ext = name.partition(".")
    if base.upper() in RESERVED_NAMES:
        return False
    return _SAFE_RE.match(name) is not None
