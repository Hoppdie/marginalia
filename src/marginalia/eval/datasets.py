"""BEIR-style dataset import and dataset-file loading."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from typing import AsyncIterator, Iterable, Mapping

from sqlalchemy import select

from marginalia.config import get_settings, resolve_profile
from marginalia.db.bootstrap import bootstrap_schema
from marginalia.db.models import AuditEvent, File, FileEntry
from marginalia.db.session import session_scope
from marginalia.eval.types import BeirDocument, BeirQuery, EvalImportResult, _ExistingEvalEntry
from marginalia.eval.utils import _read_json, _utcnow, _write_json
from marginalia.repositories import audit_events as audit_events_repo
from marginalia.semantic.index import DEFAULT_INDEX_NAME, SemanticIndexBuildResult, build_semantic_index
from marginalia.services.folders import parse_remote_folder, resolve_or_create_folder
from marginalia.storage import get_storage
from marginalia.tasks.handlers.ingest_file import handle_ingest_file
from marginalia.utils.ids import new_id, storage_prefix

def eval_root() -> Path:
    return Path(get_settings().marginalia_home).expanduser() / "eval"


async def import_beir_dataset(
    *,
    name: str,
    source_dir: Path,
    split: str = "test",
    limit: int | None = None,
    remote_folder: str | None = None,
    progress_every: int = 25,
    concurrency: int = 1,
    resume: bool = False,
) -> EvalImportResult:
    """Import a local BEIR-style dataset and synchronously ingest documents."""
    _ensure_ingest_profile()
    source_dir = source_dir.expanduser().resolve()
    corpus_path = source_dir / "corpus.jsonl"
    queries_path = source_dir / "queries.jsonl"
    qrels_path = _resolve_qrels_path(source_dir, split=split)
    _require_file(corpus_path)
    _require_file(queries_path)
    _require_file(qrels_path)

    await bootstrap_schema()

    dataset_dir = eval_root() / name
    manifest_path = dataset_dir / "manifest.json"
    if manifest_path.exists():
        raise RuntimeError(
            f"eval dataset {name!r} already exists at {dataset_dir}. "
            "Use a fresh name or remove that eval dataset before re-importing."
        )

    if dataset_dir.exists() and not resume:
        raise RuntimeError(
            f"eval dataset directory {dataset_dir} already exists without a "
            "complete manifest. Re-run with --resume to continue a partial "
            "import, or use a fresh name."
        )
    dataset_dir.mkdir(parents=True, exist_ok=resume)
    if not (dataset_dir / "queries.jsonl").exists():
        shutil.copy2(queries_path, dataset_dir / "queries.jsonl")
    if not (dataset_dir / "qrels.tsv").exists():
        shutil.copy2(qrels_path, dataset_dir / "qrels.tsv")

    query_count = sum(1 for _ in iter_beir_queries(queries_path))
    qrel_count = sum(1 for _ in iter_qrels(qrels_path))
    folder_path = remote_folder or f"/eval/{name}/"
    folder_id = await _ensure_folder(folder_path)
    concurrency = max(1, int(concurrency or 1))

    existing = await _load_existing_eval_entries(name)
    doc_map: dict[str, str] = {
        doc_id: entry.entry_id
        for doc_id, entry in existing.items()
        if entry.ingested
    }
    docs = list(iter_beir_corpus(corpus_path, limit=limit))
    pending_docs = [
        doc for doc in docs
        if not existing.get(doc.doc_id, _ExistingEvalEntry("", "", False)).ingested
    ]
    completed = len(doc_map)
    if resume and completed:
        _write_json(dataset_dir / "doc_map.json", doc_map)
        print(f"  resuming with {completed} already ingested document(s)")

    try:
        if concurrency == 1:
            for doc in pending_docs:
                doc_id, entry_id = await _import_one_beir_doc(
                    doc=doc,
                    dataset_name=name,
                    folder_id=folder_id,
                    folder_path=folder_path,
                    existing=existing.get(doc.doc_id),
                )
                doc_map[doc_id] = entry_id
                completed += 1
                if progress_every and completed % progress_every == 0:
                    _write_json(dataset_dir / "doc_map.json", doc_map)
                    print(f"  imported+ingested {completed} document(s)")
        else:
            sem = asyncio.Semaphore(concurrency)

            async def _bounded(doc: BeirDocument) -> tuple[str, str]:
                async with sem:
                    return await _import_one_beir_doc(
                        doc=doc,
                        dataset_name=name,
                        folder_id=folder_id,
                        folder_path=folder_path,
                        existing=existing.get(doc.doc_id),
                    )

            tasks = [asyncio.create_task(_bounded(doc)) for doc in pending_docs]
            try:
                for task in asyncio.as_completed(tasks):
                    doc_id, entry_id = await task
                    doc_map[doc_id] = entry_id
                    completed += 1
                    if progress_every and completed % progress_every == 0:
                        _write_json(dataset_dir / "doc_map.json", doc_map)
                        print(f"  imported+ingested {completed} document(s)")
            except Exception:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        _write_json(dataset_dir / "doc_map.json", doc_map)
        _write_json(
            manifest_path,
            {
                "name": name,
                "format": "beir",
                "source_dir": str(source_dir),
                "split": split,
                "limit": limit,
                "remote_folder": folder_path,
                "docs_imported": len(doc_map),
                "queries": query_count,
                "qrels": qrel_count,
                "concurrency": concurrency,
                "resumed": resume,
                "created_at": _utcnow().isoformat(),
            },
        )
    except Exception:
        _write_json(
            dataset_dir / "manifest.failed.json",
            {
                "name": name,
                "format": "beir",
                "source_dir": str(source_dir),
                "split": split,
                "docs_imported_before_failure": len(doc_map),
                "concurrency": concurrency,
                "resumed": resume,
                "failed_at": _utcnow().isoformat(),
            },
        )
        raise

    return EvalImportResult(
        name=name,
        dataset_dir=dataset_dir,
        docs_imported=len(doc_map),
        queries=query_count,
        qrels=qrel_count,
        split=split,
        resumed=resume,
        concurrency=concurrency,
    )


async def build_eval_semantic_index(
    *,
    name: str,
    batch_size: int | None = None,
    concurrency: int = 1,
    resume: bool = False,
    progress_every: int = 50,
) -> SemanticIndexBuildResult:
    """Build a local semantic index for an imported eval dataset."""
    await bootstrap_schema()
    dataset_dir = eval_root() / name
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"eval dataset {name!r} is not imported")
    doc_map: dict[str, str] = _read_json(dataset_dir / "doc_map.json")
    entry_ids = list(doc_map.values())
    async with session_scope() as session:
        return await build_semantic_index(
            session,
            index_name=DEFAULT_INDEX_NAME,
            entry_ids=entry_ids,
            batch_size=batch_size,
            concurrency=concurrency,
            resume=resume,
            progress_every=progress_every,
        )


def iter_beir_corpus(path: Path, *, limit: int | None = None) -> Iterable[BeirDocument]:
    count = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            doc_id = str(obj.get("_id") or obj.get("id") or "").strip()
            if not doc_id:
                continue
            yield BeirDocument(
                doc_id=doc_id,
                title=str(obj.get("title") or ""),
                text=str(obj.get("text") or ""),
                metadata=obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {},
            )
            count += 1
            if limit is not None and count >= limit:
                break


def iter_beir_queries(path: Path) -> Iterable[BeirQuery]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            query_id = str(obj.get("_id") or obj.get("id") or "").strip()
            text = str(obj.get("text") or "").strip()
            if query_id and text:
                metadata = obj.get("metadata")
                yield BeirQuery(
                    query_id=query_id,
                    text=text,
                    metadata=metadata if isinstance(metadata, dict) else {},
                )


def iter_qrels(path: Path) -> Iterable[tuple[str, str, int]]:
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            parts = line.split()
            if not parts:
                continue
            head = [p.lower() for p in parts[:3]]
            if head[:2] in (["query-id", "corpus-id"], ["query_id", "corpus_id"]):
                continue
            if len(parts) >= 3:
                if len(parts) >= 4 and parts[1] in {"0", "Q0", "q0"}:
                    qid, doc_id, rel_raw = parts[0], parts[2], parts[3]
                else:
                    qid, doc_id, rel_raw = parts[0], parts[1], parts[2]
                try:
                    relevance = int(float(rel_raw))
                except ValueError:
                    continue
                yield qid, doc_id, relevance


def load_qrels(path: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for qid, doc_id, relevance in iter_qrels(path):
        out.setdefault(qid, {})[doc_id] = relevance
    return out


def _ensure_ingest_profile() -> None:
    profile = resolve_profile(get_settings(), "ingest")
    if not profile.api_key:
        raise RuntimeError(
            "LLM ingest profile is not configured. Set LLM_DEFAULT_API_KEY "
            "or LLM_INGEST_API_KEY before importing an eval dataset."
        )


async def _ensure_folder(remote_folder: str) -> str | None:
    segments = parse_remote_folder(remote_folder)
    async with session_scope() as session:
        folder = await resolve_or_create_folder(session, segments)
        await session.commit()
        return folder.id if folder is not None else None


async def _load_existing_eval_entries(
    dataset_name: str,
) -> dict[str, _ExistingEvalEntry]:
    """Recover doc_id -> file/entry mapping from audit events for resume."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(AuditEvent.kind, AuditEvent.payload)
                .where(AuditEvent.kind.in_(("file_created", "entry_created")))
            )
        ).all()

        file_by_doc: dict[str, str] = {}
        entry_by_doc: dict[str, str] = {}
        for kind, payload in rows:
            if not isinstance(payload, Mapping):
                continue
            if payload.get("source") != "eval_import":
                continue
            if payload.get("dataset") != dataset_name:
                continue
            doc_id = str(payload.get("doc_id") or "")
            if not doc_id:
                continue
            if kind == "file_created" and payload.get("file_id"):
                file_by_doc[doc_id] = str(payload["file_id"])
            elif kind == "entry_created" and payload.get("entry_id"):
                entry_by_doc[doc_id] = str(payload["entry_id"])

        file_ids = list(file_by_doc.values())
        ingested_by_file: dict[str, bool] = {}
        if file_ids:
            file_rows = (
                await session.execute(
                    select(File.id, File.ingested_at).where(File.id.in_(file_ids))
                )
            ).all()
            ingested_by_file = {
                file_id: ingested_at is not None
                for file_id, ingested_at in file_rows
            }
        await session.commit()

    out: dict[str, _ExistingEvalEntry] = {}
    for doc_id, file_id in file_by_doc.items():
        entry_id = entry_by_doc.get(doc_id)
        if not entry_id:
            continue
        out[doc_id] = _ExistingEvalEntry(
            file_id=file_id,
            entry_id=entry_id,
            ingested=bool(ingested_by_file.get(file_id)),
        )
    return out


async def _import_one_beir_doc(
    *,
    doc: BeirDocument,
    dataset_name: str,
    folder_id: str | None,
    folder_path: str,
    existing: _ExistingEvalEntry | None,
) -> tuple[str, str]:
    if existing is not None:
        await _ingest_eval_file_with_retries(existing.file_id)
        return doc.doc_id, existing.entry_id

    file_id, entry_id = await _create_eval_text_entry(
        doc=doc,
        dataset_name=dataset_name,
        folder_id=folder_id,
        folder_path=folder_path,
    )
    await _ingest_eval_file_with_retries(file_id)
    return doc.doc_id, entry_id


async def _ingest_eval_file_with_retries(file_id: str, *, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        try:
            await handle_ingest_file({"file_id": file_id})
            return
        except Exception:
            if attempt >= attempts:
                raise
            await asyncio.sleep(min(10.0, 0.5 * (2 ** (attempt - 1))))


async def _create_eval_text_entry(
    *,
    doc: BeirDocument,
    dataset_name: str,
    folder_id: str | None,
    folder_path: str,
) -> tuple[str, str]:
    now = _utcnow()
    file_id = new_id()
    entry_id = new_id()
    display_name = _doc_display_name(doc.doc_id)
    body = _render_document(doc)
    data = body.encode("utf-8")
    sha256 = hashlib.sha256(data).hexdigest()
    top, sub = storage_prefix(file_id)
    suggested_key = f"{top}/{sub}/{file_id}"
    storage_key = await get_storage().put(
        suggested_key,
        _one_chunk(data),
        size=len(data),
        content_type="text/plain",
        display_name=display_name,
        folder_path=folder_path,
    )

    async with session_scope() as session:
        session.add(File(
            id=file_id,
            storage_key=storage_key,
            sha256=sha256,
            size_bytes=len(data),
            mime_type="text/plain",
            original_ext=".txt",
            kind=None,
            summary=None,
            description=None,
            extra=None,
            ingest_status="pending",
            ingested_at=None,
            deleted_at=None,
            created_at=now,
            updated_at=now,
        ))
        session.add(FileEntry(
            id=entry_id,
            folder_id=folder_id,
            file_id=file_id,
            display_name=display_name,
            lifecycle="active",
            catalog_id=None,
            extra=None,
            deleted_at=None,
            purge_after=None,
            created_at=now,
            updated_at=now,
        ))
        await audit_events_repo.append(
            session,
            kind="file_created",
            payload={
                "file_id": file_id,
                "sha256": sha256,
                "size_bytes": len(data),
                "mime_type": "text/plain",
                "source": "eval_import",
                "dataset": dataset_name,
                "doc_id": doc.doc_id,
            },
        )
        await audit_events_repo.append(
            session,
            kind="entry_created",
            payload={
                "entry_id": entry_id,
                "folder_id": folder_id,
                "file_id": file_id,
                "display_name": display_name,
                "deduped": False,
                "source": "eval_import",
                "dataset": dataset_name,
                "doc_id": doc.doc_id,
            },
        )
        await session.commit()
    return file_id, entry_id


async def _one_chunk(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _render_document(doc: BeirDocument) -> str:
    lines = [f"Document ID: {doc.doc_id}"]
    if doc.title.strip():
        lines.extend(["", f"# {doc.title.strip()}"])
    if doc.metadata:
        lines.extend(["", "Metadata:", json.dumps(doc.metadata, ensure_ascii=False)])
    if doc.text.strip():
        lines.extend(["", doc.text.strip()])
    return "\n".join(lines).strip() + "\n"


def _doc_display_name(doc_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in doc_id)
    safe = safe.strip("._") or hashlib.sha1(doc_id.encode("utf-8")).hexdigest()[:16]
    if not safe.lower().endswith(".txt"):
        safe += ".txt"
    return safe[:255]


def _resolve_qrels_path(source_dir: Path, *, split: str) -> Path:
    direct = source_dir / "qrels.tsv"
    if direct.exists():
        return direct
    split_path = source_dir / "qrels" / f"{split}.tsv"
    if split_path.exists():
        return split_path
    qrels_dir = source_dir / "qrels"
    if qrels_dir.is_dir():
        candidates = sorted(qrels_dir.glob("*.tsv"))
        if len(candidates) == 1:
            return candidates[0]
    return split_path


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
