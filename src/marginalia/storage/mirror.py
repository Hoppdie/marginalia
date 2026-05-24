"""Mirror storage backend — folder-tree on disk that matches user intent.

Storage layout under MARGINALIA_HOME/library/:

    research/llm/paper.pdf
    notes/2026-05/meeting.md
    photos/IMG_001.jpg
    bundle.tar.gz

Storage_key in the db is the relative posix path (e.g.
'research/llm/paper.pdf'). Sanitization happens at put-time and
collisions are resolved with ' (2)', ' (3)' suffixes.

This backend is the new default for local installs because:
  - users can browse / open / rsync / git the vault directly
  - no UUID indirection means the vault survives marginalia removal
  - ingest stays uniform: pipelines call storage.get(key) and don't
    care which backend is in play

Trade-offs vs local UUID-flat:
  - dedup is OFF (handled at the upload service layer based on
    backend type) — same bytes uploaded twice = two files
  - rename / move costs an extra disk op (transactional with db)
  - unicode / cross-platform filename quirks need sanitize()
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator

import aiofiles
import aiofiles.os

from marginalia.storage.base import StorageBackend
from marginalia.storage.sanitize import sanitize_folder, sanitize_name

_CHUNK = 1024 * 256
_COLLISION_LIMIT = 10_000  # sanity cap


class MirrorStorage(StorageBackend):
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _abs(self, key: str) -> Path:
        # Defence-in-depth: refuse keys that escape the vault root.
        # storage_key is supposed to come out of put()/rename(), where
        # we control sanitization, but verifying here means a corrupt
        # db row can't trick us into reading /etc/passwd.
        candidate = (self.root / key).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError(
                f"storage_key {key!r} escapes vault root"
            ) from exc
        return candidate

    async def put(
        self,
        key: str,
        stream: AsyncIterator[bytes],
        *,
        size: int | None = None,
        content_type: str | None = None,
        display_name: str | None = None,
        folder_path: str | None = None,
    ) -> str:
        # Mirror ignores `key` and computes a path from the hint pair.
        # If the caller didn't give a display_name we fall back to the
        # `key` they suggested — that lets accidental old call-sites
        # still write something sensible (UUID basename).
        target_rel = _resolve_path(
            display_name=display_name or os.path.basename(key) or "unnamed",
            folder_path=folder_path,
            existing=self._exists_sync,
        )
        target = self._abs(target_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        async with aiofiles.open(tmp, "wb") as f:
            async for chunk in stream:
                await f.write(chunk)
        os.replace(tmp, target)
        return target_rel

    async def get(self, key: str) -> AsyncIterator[bytes]:
        async with aiofiles.open(self._abs(key), "rb") as f:
            while True:
                chunk = await f.read(_CHUNK)
                if not chunk:
                    return
                yield chunk

    async def get_range(self, key: str, start: int, end: int) -> bytes:
        length = max(0, end - start + 1)
        async with aiofiles.open(self._abs(key), "rb") as f:
            await f.seek(start)
            return await f.read(length)

    async def delete(self, key: str) -> None:
        try:
            await aiofiles.os.remove(self._abs(key))
        except FileNotFoundError:
            pass

    async def exists(self, key: str) -> bool:
        return await aiofiles.os.path.isfile(self._abs(key))

    async def rename(self, old_key: str, new_key: str) -> str:
        """Move on disk. `new_key` is a relative path, possibly with
        the desired display_name embedded; we re-sanitize and resolve
        collisions just like put()."""
        old_abs = self._abs(old_key)
        new_rel = _resolve_path(
            display_name=os.path.basename(new_key),
            folder_path=os.path.dirname(new_key),
            existing=self._exists_sync,
            skip=old_key,
        )
        new_abs = self._abs(new_rel)
        new_abs.parent.mkdir(parents=True, exist_ok=True)
        if old_abs == new_abs:
            return old_key
        os.replace(old_abs, new_abs)
        # best-effort cleanup of newly empty parents
        try:
            old_abs.parent.relative_to(self.root)
            for parent in old_abs.parents:
                if parent == self.root:
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break
        except ValueError:
            pass
        return new_rel

    def _exists_sync(self, rel: str) -> bool:
        return self._abs(rel).exists()


def _resolve_path(
    *,
    display_name: str,
    folder_path: str | None,
    existing,
    skip: str | None = None,
) -> str:
    """Build a sanitized relative path for a display_name in folder_path,
    appending ' (N)' before the extension on collision until a free slot
    is found. `existing(rel)` returns True if the path is taken; pass
    `skip` to ignore a known-current path (used by rename)."""
    safe_folder = sanitize_folder(folder_path or "")
    safe_name = sanitize_name(display_name)
    rel = _join(safe_folder, safe_name)
    if (skip is not None and rel == skip) or not existing(rel):
        return rel

    stem, dot, ext = safe_name.rpartition(".")
    if not dot:
        stem, ext = safe_name, ""
    else:
        ext = f".{ext}"
    for n in range(2, _COLLISION_LIMIT):
        candidate = f"{stem} ({n}){ext}"
        rel = _join(safe_folder, candidate)
        if (skip is not None and rel == skip) or not existing(rel):
            return rel
    raise RuntimeError(
        f"could not resolve mirror path collision for {display_name!r} "
        f"after {_COLLISION_LIMIT} attempts"
    )


def _join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name
