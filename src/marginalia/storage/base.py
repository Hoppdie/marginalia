from __future__ import annotations

from typing import AsyncIterator, Protocol


class StorageBackend(Protocol):
    """Pluggable object storage. Implementations: local filesystem
    (UUID-flat object pool), mirror filesystem (folder-tree under
    MARGINALIA_HOME), S3/MinIO.

    `put` accepts an optional (display_name, folder_path) hint pair.
    Local + S3 ignore them — they keep using the explicit `key`.
    Mirror uses them to build the on-disk path; `key` is then ignored
    on input and the FINAL relative path is returned.

    Returns the final storage_key the caller should persist to db.
    Local + S3 always return `key` unchanged. Mirror may return a
    different value (after sanitize / collision-resolution).
    """

    async def put(
        self,
        key: str,
        stream: AsyncIterator[bytes],
        *,
        size: int | None = None,
        content_type: str | None = None,
        display_name: str | None = None,
        folder_path: str | None = None,
    ) -> str: ...

    async def get(self, key: str) -> AsyncIterator[bytes]: ...

    async def get_range(self, key: str, start: int, end: int) -> bytes:
        """Return bytes [start, end] inclusive (HTTP Range semantics)."""
        ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def rename(self, old_key: str, new_key: str) -> str:
        """Rename / move an object. Returns the final new key (which
        may differ from `new_key` if the backend resolves collisions).

        Local + S3 implementations are best-effort — they don't really
        need rename since storage_key is a UUID. Mirror uses this for
        on-disk move when display_name or folder_path changes.
        """
        ...
