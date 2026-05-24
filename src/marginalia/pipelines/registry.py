"""Pipeline registry: route (mime, ext, filename) → Pipeline.

Each pipeline self-registers via `@register_pipeline(...)`. The handler
asks `resolve_pipeline(mime, ext, filename=...)`; the first matching
registered entry wins.

Match precedence:
  1. extension match where the registration set `ext_overrides_mime=True`
     (used when a generic mime like text/plain would otherwise win — e.g.
     `.log` should route to log pipeline, not text)
  2. ext_patterns match where `ext_overrides_mime=True` (regex-shaped
     extensions for logrotate variants like `.log.1`, `.log-20260524`)
  3. exact mime match
  4. mime prefix match (e.g. "text/" matches "text/markdown")
  5. extension match (case-insensitive, with leading dot)
  6. ext_patterns match (regex)
  7. fallback (a pipeline registered with `fallback=True`)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from marginalia.pipelines.base import Pipeline

log = logging.getLogger(__name__)


@dataclass(slots=True)
class _Registration:
    pipeline: Pipeline
    mimes: tuple[str, ...] = ()
    mime_prefixes: tuple[str, ...] = ()
    exts: tuple[str, ...] = ()
    ext_patterns: tuple[re.Pattern[str], ...] = ()
    ext_overrides_mime: bool = False
    fallback: bool = False


_REGISTRY: list[_Registration] = []


def register_pipeline(
    *,
    mimes: tuple[str, ...] = (),
    mime_prefixes: tuple[str, ...] = (),
    exts: tuple[str, ...] = (),
    ext_patterns: tuple[re.Pattern[str], ...] = (),
    ext_overrides_mime: bool = False,
    fallback: bool = False,
) -> Callable[[type[Pipeline]], type[Pipeline]]:
    """Class decorator. Instantiates the pipeline (no-arg ctor) and registers it.

    Field roles:
      mimes / mime_prefixes / exts
        Standard registration. exts are case-insensitive, matched on the
        full final-extension token (`.log` matches `notes.log` but not
        `notes.log.1`).
      ext_patterns
        Compiled regexes matched against the **full filename**. Use this
        for variants the static `exts` list cannot capture: logrotate
        sequence numbers (`.log.1`), date-suffixed logs (`.log-20260524`).
      ext_overrides_mime=True
        Promotes both `exts` and `ext_patterns` to the *front* of the
        match precedence — checked even before mime. Use for formats
        whose real-world mime is generic (`text/plain`).
    """
    norm_exts = tuple(e.lower() if e.startswith(".") else f".{e.lower()}" for e in exts)

    def decorator(cls: type[Pipeline]) -> type[Pipeline]:
        instance = cls()  # type: ignore[call-arg]
        _REGISTRY.append(
            _Registration(
                pipeline=instance,
                mimes=mimes,
                mime_prefixes=mime_prefixes,
                exts=norm_exts,
                ext_patterns=ext_patterns,
                ext_overrides_mime=ext_overrides_mime,
                fallback=fallback,
            )
        )
        log.debug("registered pipeline %s (mimes=%s exts=%s patterns=%d "
                  "fallback=%s)",
                  instance.name, mimes, exts, len(ext_patterns), fallback)
        return cls

    return decorator


def resolve_pipeline(
    mime: str | None,
    ext: str | None,
    *,
    filename: str | None = None,
) -> Pipeline | None:
    mime = mime or ""
    ext_l = (ext or "").lower()
    if ext_l and not ext_l.startswith("."):
        ext_l = "." + ext_l
    fname = (filename or "").lower()

    # 1. ext-overrides-mime exact ext (e.g. .log → log pipeline)
    if ext_l:
        for r in _REGISTRY:
            if r.ext_overrides_mime and ext_l in r.exts:
                return r.pipeline
    # 2. ext-overrides-mime patterns (e.g. .log.1 → log pipeline)
    if fname:
        for r in _REGISTRY:
            if r.ext_overrides_mime and r.ext_patterns:
                if any(p.search(fname) for p in r.ext_patterns):
                    return r.pipeline
    # 3. exact mime
    for r in _REGISTRY:
        if mime and mime in r.mimes:
            return r.pipeline
    # 4. mime prefix
    for r in _REGISTRY:
        for prefix in r.mime_prefixes:
            if mime.startswith(prefix):
                return r.pipeline
    # 5. extension
    for r in _REGISTRY:
        if ext_l and ext_l in r.exts:
            return r.pipeline
    # 6. ext_patterns (non-overriding)
    if fname:
        for r in _REGISTRY:
            if r.ext_patterns:
                if any(p.search(fname) for p in r.ext_patterns):
                    return r.pipeline
    # 7. fallback
    for r in _REGISTRY:
        if r.fallback:
            return r.pipeline
    return None


def registered_pipelines() -> list[str]:
    return [r.pipeline.name for r in _REGISTRY]
