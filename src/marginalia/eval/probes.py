"""Answer and report comparison eval probes."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from marginalia.agent.runtime import run_turn
from marginalia.agent.tools import ToolContext
from marginalia.agent.tools.read_entries_metadata import read_entries_metadata
from marginalia.agent.tools.read_files import read_files
from marginalia.config import get_settings, resolve_profile
from marginalia.db.bootstrap import bootstrap_schema
from marginalia.db.session import session_scope
from marginalia.eval.datasets import eval_root, iter_beir_queries, load_qrels
from marginalia.eval.probe_utils import (
    _collect_read_text,
    _compact_description,
    _expected_labels,
    _extract_cited_entry_ids,
    _predict_answer_label,
    _query_patterns,
)
from marginalia.eval.prompts import (
    _complete_answer_probe,
    _complete_report_probe,
    _judge_report_pair,
    _render_react_report_user_prompt,
)
from marginalia.eval.reporting import (
    _answer_run_result,
    _report_compare_result,
    answer_probe_to_dict,
)
from marginalia.eval.retrieval import _retrieve_entries, _retrieve_entries_many_detail
from marginalia.eval.types import BeirQuery, EvalAnswerProbeResult, EvalAnswerRunResult, EvalReportCompareResult
from marginalia.eval.utils import _parse_json_object, _read_json, _truncate
from marginalia.repositories import sessions as sessions_repo

async def run_answer_probe(
    *,
    name: str,
    retriever: str = "recall_knowledge",
    query_id: str | None = None,
    query: str | None = None,
    retrieval_limit: int = 20,
    evidence_limit: int = 10,
    evidence_chars: int = 2_000,
    timeout_seconds: float = 300.0,
    max_tokens: int = 700,
    profile: str = "chat",
) -> EvalAnswerProbeResult:
    """Run a bounded final-answer probe for one eval query.

    This is intentionally not the full interactive agent. It exercises the
    report-critical path directly: retrieve candidates, read bounded source
    text, make one final-answer LLM call, then check whether the answer cited
    an entry mapped to a relevant qrels document.
    """
    _ensure_answer_profile(profile)
    await bootstrap_schema()
    dataset_dir = eval_root() / name
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"eval dataset {name!r} is not imported")

    doc_map: dict[str, str] = _read_json(dataset_dir / "doc_map.json")
    entry_to_doc = {entry_id: doc_id for doc_id, entry_id in doc_map.items()}
    queries = list(iter_beir_queries(dataset_dir / "queries.jsonl"))
    qrels = load_qrels(dataset_dir / "qrels.tsv")
    selected_query_id, selected_query, selected_metadata = _select_answer_query(
        queries=queries,
        qrels=qrels,
        doc_map=doc_map,
        query_id=query_id,
        query=query,
    )
    relevant_doc_ids = sorted(
        doc_id
        for doc_id, rel in qrels.get(selected_query_id or "", {}).items()
        if doc_id in doc_map and rel > 0
    )
    expected_labels = _expected_labels(selected_metadata, relevant_doc_ids)

    timeout = timeout_seconds if timeout_seconds > 0 else None
    started = time.monotonic()
    try:
        return await asyncio.wait_for(
            _run_answer_probe_inner(
                name=name,
                retriever=retriever,
                query_id=selected_query_id,
                query=selected_query,
                retrieval_limit=max(1, retrieval_limit),
                evidence_limit=max(1, evidence_limit),
                evidence_chars=max(500, evidence_chars),
                max_tokens=max(128, max_tokens),
                profile=profile,
                entry_to_doc=entry_to_doc,
                relevant_doc_ids=relevant_doc_ids,
                expected_labels=expected_labels,
                timeout_seconds=timeout_seconds,
                started=started,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return EvalAnswerProbeResult(
            name=name,
            retriever=retriever,
            query_id=selected_query_id,
            query=selected_query,
            timed_out=True,
            timeout_seconds=timeout_seconds,
            elapsed_ms=elapsed_ms,
            retrieval_limit=max(1, retrieval_limit),
            evidence_limit=max(1, evidence_limit),
            relevant_doc_ids=relevant_doc_ids,
            ranked_doc_ids=[],
            evidence_doc_ids=[],
            cited_entry_ids=[],
            cited_doc_ids=[],
            expected_labels=expected_labels,
            predicted_label=None,
            label_correct=False if expected_labels else None,
            first_relevant_rank=None,
            evidence_contains_relevant=False,
            answer_cites_relevant=False,
            answer="",
            usage={},
            error=f"answer probe exceeded {timeout_seconds:.1f}s",
        )


async def run_answer_eval_dataset(
    *,
    name: str,
    retriever: str = "recall_knowledge",
    retrieval_limit: int = 20,
    evidence_limit: int = 10,
    evidence_chars: int = 2_000,
    timeout_seconds: float = 300.0,
    max_tokens: int = 700,
    profile: str = "chat",
    query_limit: int | None = None,
    qrels_only: bool = False,
    concurrency: int = 1,
) -> EvalAnswerRunResult:
    """Run bounded final-answer probes across imported eval queries."""
    _ensure_answer_profile(profile)
    await bootstrap_schema()
    dataset_dir = eval_root() / name
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"eval dataset {name!r} is not imported")

    doc_map: dict[str, str] = _read_json(dataset_dir / "doc_map.json")
    entry_to_doc = {entry_id: doc_id for doc_id, entry_id in doc_map.items()}
    qrels = load_qrels(dataset_dir / "qrels.tsv")
    queries = list(iter_beir_queries(dataset_dir / "queries.jsonl"))
    if qrels_only:
        queries = [
            q for q in queries
            if any(
                rel > 0 and doc_id in doc_map
                for doc_id, rel in qrels.get(q.query_id, {}).items()
            )
        ]
    if query_limit is not None:
        queries = queries[:query_limit]

    timeout = timeout_seconds if timeout_seconds > 0 else None
    concurrency = max(1, int(concurrency or 1))
    semaphore = asyncio.Semaphore(concurrency)
    total_started = time.monotonic()
    work_items: list[tuple[BeirQuery, list[str]]] = []
    skipped = 0
    for q in queries:
        relevant_doc_ids = sorted(
            doc_id
            for doc_id, rel in qrels.get(q.query_id, {}).items()
            if doc_id in doc_map and rel > 0
        )
        if not relevant_doc_ids:
            skipped += 1
            continue
        work_items.append((q, relevant_doc_ids))

    precomputed_ranked: dict[str, list[str]] = {}
    precomputed_evidence: dict[str, list[str]] = {}
    if retriever == "recall_knowledge" and work_items:
        async with session_scope() as session:
            retrieved_many = await _retrieve_entries_many_detail(
                session,
                retriever=retriever,
                queries=[q.text for q, _relevant_doc_ids in work_items],
                limit=max(1, retrieval_limit),
                evidence_limit=max(1, evidence_limit),
            )
        precomputed_ranked = {
            q.query_id: retrieved["ranked_ids"]
            for (q, _relevant_doc_ids), retrieved in zip(work_items, retrieved_many)
        }
        precomputed_evidence = {
            q.query_id: retrieved["evidence_ids"]
            for (q, _relevant_doc_ids), retrieved in zip(work_items, retrieved_many)
        }

    async def _run_one(q: BeirQuery, relevant_doc_ids: list[str]) -> dict[str, Any]:
        async with semaphore:
            started = time.monotonic()
            expected_labels = _expected_labels(q.metadata, relevant_doc_ids)
            try:
                probe = await asyncio.wait_for(
                    _run_answer_probe_inner(
                        name=name,
                        retriever=retriever,
                        query_id=q.query_id,
                        query=q.text,
                        retrieval_limit=max(1, retrieval_limit),
                        evidence_limit=max(1, evidence_limit),
                        evidence_chars=max(500, evidence_chars),
                        max_tokens=max(128, max_tokens),
                        profile=profile,
                        entry_to_doc=entry_to_doc,
                        relevant_doc_ids=relevant_doc_ids,
                        expected_labels=expected_labels,
                        timeout_seconds=timeout_seconds,
                        started=started,
                        ranked_entries=precomputed_ranked.get(q.query_id),
                        evidence_entry_ids=precomputed_evidence.get(q.query_id),
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                probe = EvalAnswerProbeResult(
                    name=name,
                    retriever=retriever,
                    query_id=q.query_id,
                    query=q.text,
                    timed_out=True,
                    timeout_seconds=timeout_seconds,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    retrieval_limit=max(1, retrieval_limit),
                    evidence_limit=max(1, evidence_limit),
                    relevant_doc_ids=relevant_doc_ids,
                    ranked_doc_ids=[],
                    evidence_doc_ids=[],
                    cited_entry_ids=[],
                    cited_doc_ids=[],
                    expected_labels=expected_labels,
                    predicted_label=None,
                    label_correct=False if expected_labels else None,
                    first_relevant_rank=None,
                    evidence_contains_relevant=False,
                    answer_cites_relevant=False,
                    answer="",
                    usage={},
                    error=f"answer probe exceeded {timeout_seconds:.1f}s",
                )
            return answer_probe_to_dict(probe)

    per_query = await asyncio.gather(
        *(_run_one(q, relevant_doc_ids) for q, relevant_doc_ids in work_items)
    )

    return _answer_run_result(
        name=name,
        retriever=retriever,
        queries_total=len(queries),
        queries_skipped=skipped,
        timeout_seconds=timeout_seconds,
        concurrency=concurrency,
        total_elapsed_ms=int((time.monotonic() - total_started) * 1000),
        per_query=per_query,
    )


async def run_report_compare_dataset(
    *,
    name: str,
    retriever: str = "recall_knowledge",
    retrieval_limit: int = 20,
    evidence_limit: int = 10,
    evidence_chars: int = 2_000,
    timeout_seconds: float = 300.0,
    max_tokens: int = 900,
    profile: str = "chat",
    judge_profile: str = "chat",
    query_limit: int | None = 30,
    qrels_only: bool = True,
    concurrency: int = 1,
) -> EvalReportCompareResult:
    """Compare one-shot RAG reports with full ReAct reports on the same queries."""
    _ensure_answer_profile(profile)
    _ensure_answer_profile(judge_profile)
    await bootstrap_schema()
    dataset_dir = eval_root() / name
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"eval dataset {name!r} is not imported")

    doc_map: dict[str, str] = _read_json(dataset_dir / "doc_map.json")
    entry_to_doc = {entry_id: doc_id for doc_id, entry_id in doc_map.items()}
    qrels = load_qrels(dataset_dir / "qrels.tsv")
    queries = list(iter_beir_queries(dataset_dir / "queries.jsonl"))
    if qrels_only:
        queries = [
            q for q in queries
            if any(
                rel > 0 and doc_id in doc_map
                for doc_id, rel in qrels.get(q.query_id, {}).items()
            )
        ]
    if query_limit is not None:
        queries = queries[:query_limit]

    work_items: list[tuple[BeirQuery, list[str]]] = []
    skipped = 0
    for q in queries:
        relevant_doc_ids = sorted(
            doc_id
            for doc_id, rel in qrels.get(q.query_id, {}).items()
            if doc_id in doc_map and rel > 0
        )
        if not relevant_doc_ids:
            skipped += 1
            continue
        work_items.append((q, relevant_doc_ids))

    precomputed_ranked: dict[str, list[str]] = {}
    precomputed_evidence: dict[str, list[str]] = {}
    if work_items:
        async with session_scope() as session:
            retrieved_many = await _retrieve_entries_many_detail(
                session,
                retriever=retriever,
                queries=[q.text for q, _relevant_doc_ids in work_items],
                limit=max(1, retrieval_limit),
                evidence_limit=max(1, evidence_limit),
            )
        precomputed_ranked = {
            q.query_id: retrieved["ranked_ids"]
            for (q, _relevant_doc_ids), retrieved in zip(work_items, retrieved_many)
        }
        precomputed_evidence = {
            q.query_id: retrieved["evidence_ids"]
            for (q, _relevant_doc_ids), retrieved in zip(work_items, retrieved_many)
        }

    timeout = timeout_seconds if timeout_seconds > 0 else None
    semaphore = asyncio.Semaphore(max(1, int(concurrency or 1)))
    total_started = time.monotonic()

    async def _run_one(q: BeirQuery, relevant_doc_ids: list[str]) -> dict[str, Any]:
        async with semaphore:
            started = time.monotonic()
            expected_labels = _expected_labels(q.metadata, relevant_doc_ids)
            ranked_entries = precomputed_ranked.get(q.query_id) or []
            evidence_entry_ids = precomputed_evidence.get(q.query_id) or []
            try:
                rag, react, judge = await asyncio.wait_for(
                    _run_report_compare_one(
                        name=name,
                        retriever=retriever,
                        query_id=q.query_id,
                        query=q.text,
                        retrieval_limit=max(1, retrieval_limit),
                        evidence_limit=max(1, evidence_limit),
                        evidence_chars=max(500, evidence_chars),
                        max_tokens=max(128, max_tokens),
                        profile=profile,
                        judge_profile=judge_profile,
                        entry_to_doc=entry_to_doc,
                        relevant_doc_ids=relevant_doc_ids,
                        expected_labels=expected_labels,
                        timeout_seconds=timeout_seconds,
                        started=started,
                        ranked_entries=ranked_entries,
                        evidence_entry_ids=evidence_entry_ids,
                    ),
                    timeout=timeout,
                )
                timed_out = False
                error = None
            except asyncio.TimeoutError:
                rag = _empty_report_side("rag")
                react = _empty_report_side("react")
                judge = {
                    "winner": "tie",
                    "scores": {},
                    "reason": f"compare-report exceeded {timeout_seconds:.1f}s",
                    "usage": {},
                    "error": "timeout",
                }
                timed_out = True
                error = f"compare-report exceeded {timeout_seconds:.1f}s"
            except Exception as exc:  # noqa: BLE001
                rag = _empty_report_side("rag")
                react = _empty_report_side("react")
                judge = {
                    "winner": "tie",
                    "scores": {},
                    "reason": str(exc)[:300],
                    "usage": {},
                    "error": repr(exc),
                }
                timed_out = False
                error = repr(exc)

            return {
                "query_id": q.query_id,
                "query": q.text,
                "expected_labels": expected_labels,
                "relevant_doc_ids": relevant_doc_ids,
                "timed_out": timed_out,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "error": error,
                "rag": rag,
                "react": react,
                "judge": judge,
            }

    per_query = await asyncio.gather(
        *(_run_one(q, relevant_doc_ids) for q, relevant_doc_ids in work_items)
    )
    return _report_compare_result(
        name=name,
        queries_total=len(queries),
        queries_skipped=skipped,
        timeout_seconds=timeout_seconds,
        concurrency=max(1, int(concurrency or 1)),
        total_elapsed_ms=int((time.monotonic() - total_started) * 1000),
        per_query=per_query,
    )


async def _run_report_compare_one(
    *,
    name: str,
    retriever: str,
    query_id: str,
    query: str,
    retrieval_limit: int,
    evidence_limit: int,
    evidence_chars: int,
    max_tokens: int,
    profile: str,
    judge_profile: str,
    entry_to_doc: Mapping[str, str],
    relevant_doc_ids: list[str],
    expected_labels: list[str],
    timeout_seconds: float,
    started: float,
    ranked_entries: list[str],
    evidence_entry_ids: list[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    rag = await _run_rag_report_probe(
        name=name,
        retriever=retriever,
        query_id=query_id,
        query=query,
        retrieval_limit=retrieval_limit,
        evidence_limit=evidence_limit,
        evidence_chars=evidence_chars,
        max_tokens=max_tokens,
        profile=profile,
        entry_to_doc=entry_to_doc,
        relevant_doc_ids=relevant_doc_ids,
        expected_labels=expected_labels,
        timeout_seconds=timeout_seconds,
        started=started,
        ranked_entries=ranked_entries,
        evidence_entry_ids=evidence_entry_ids,
    )
    react = await _run_react_report_probe(
        name=name,
        query_id=query_id,
        query=query,
        profile=profile,
        entry_to_doc=entry_to_doc,
        relevant_doc_ids=relevant_doc_ids,
        expected_labels=expected_labels,
        timeout_seconds=timeout_seconds,
    )
    judge = await _judge_report_pair(
        query=query,
        rag_answer=rag["answer"],
        react_answer=react["answer"],
        expected_labels=expected_labels,
        profile=judge_profile,
    )
    return rag, react, judge


def _ensure_answer_profile(profile_name: str) -> None:
    profile = resolve_profile(get_settings(), profile_name)
    if not profile.api_key:
        env_name = f"LLM_{profile_name.upper()}_API_KEY"
        raise RuntimeError(
            f"LLM {profile_name!r} profile is not configured. Set "
            f"LLM_DEFAULT_API_KEY or {env_name} before running answer eval."
        )


def _select_answer_query(
    *,
    queries: list[BeirQuery],
    qrels: Mapping[str, Mapping[str, int]],
    doc_map: Mapping[str, str],
    query_id: str | None,
    query: str | None,
) -> tuple[str | None, str, dict[str, Any]]:
    if query is not None and query.strip():
        metadata = {}
        if query_id:
            metadata = next(
                (q.metadata for q in queries if q.query_id == query_id),
                {},
            )
        return query_id, query.strip(), metadata

    by_id = {q.query_id: q for q in queries}
    if query_id:
        if query_id not in by_id:
            raise RuntimeError(f"query_id {query_id!r} not found in eval dataset")
        selected = by_id[query_id]
        return query_id, selected.text, selected.metadata

    for q in queries:
        relevant = [
            doc_id
            for doc_id, rel in qrels.get(q.query_id, {}).items()
            if rel > 0 and doc_id in doc_map
        ]
        if relevant:
            return q.query_id, q.text, q.metadata
    if queries:
        return queries[0].query_id, queries[0].text, queries[0].metadata
    raise RuntimeError("eval dataset has no queries")


async def _run_answer_probe_inner(
    *,
    name: str,
    retriever: str,
    query_id: str | None,
    query: str,
    retrieval_limit: int,
    evidence_limit: int,
    evidence_chars: int,
    max_tokens: int,
    profile: str,
    entry_to_doc: Mapping[str, str],
    relevant_doc_ids: list[str],
    expected_labels: list[str],
    timeout_seconds: float,
    started: float,
    ranked_entries: list[str] | None = None,
    evidence_entry_ids: list[str] | None = None,
) -> EvalAnswerProbeResult:
    async with session_scope() as session:
        if ranked_entries is None:
            ranked_entries = await _retrieve_entries(
                session,
                retriever=retriever,
                query=query,
                limit=retrieval_limit,
            )
        if evidence_entry_ids is None:
            evidence_entry_ids = [
                eid for eid in ranked_entries if eid in entry_to_doc
            ][:evidence_limit]
        else:
            evidence_entry_ids = [
                eid for eid in evidence_entry_ids if eid in entry_to_doc
            ][:evidence_limit]
        evidence = await _read_answer_evidence(
            session,
            query=query,
            entry_ids=evidence_entry_ids,
            entry_to_doc=entry_to_doc,
            evidence_chars=evidence_chars,
        )

    ranked_doc_ids = [
        entry_to_doc[eid]
        for eid in ranked_entries
        if eid in entry_to_doc
    ]
    evidence_doc_ids = [
        entry_to_doc[eid]
        for eid in evidence_entry_ids
        if eid in entry_to_doc
    ]
    rel_set = set(relevant_doc_ids)
    first_relevant_rank = next(
        (idx + 1 for idx, doc_id in enumerate(ranked_doc_ids) if doc_id in rel_set),
        None,
    )
    answer, usage = await _complete_answer_probe(
        query=query,
        evidence=evidence,
        profile=profile,
        max_tokens=max_tokens,
    )
    cited_entry_ids = _extract_cited_entry_ids(
        answer,
        known_entry_ids=entry_to_doc.keys(),
    )
    cited_doc_ids = [
        entry_to_doc[eid]
        for eid in cited_entry_ids
        if eid in entry_to_doc
    ]
    predicted_label = _predict_answer_label(answer)
    label_correct = (
        predicted_label in set(expected_labels)
        if expected_labels and predicted_label is not None
        else (False if expected_labels else None)
    )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return EvalAnswerProbeResult(
        name=name,
        retriever=retriever,
        query_id=query_id,
        query=query,
        timed_out=False,
        timeout_seconds=timeout_seconds,
        elapsed_ms=elapsed_ms,
        retrieval_limit=retrieval_limit,
        evidence_limit=evidence_limit,
        relevant_doc_ids=relevant_doc_ids,
        ranked_doc_ids=ranked_doc_ids,
        evidence_doc_ids=evidence_doc_ids,
        cited_entry_ids=cited_entry_ids,
        cited_doc_ids=cited_doc_ids,
        expected_labels=expected_labels,
        predicted_label=predicted_label,
        label_correct=label_correct,
        first_relevant_rank=first_relevant_rank,
        evidence_contains_relevant=bool(set(evidence_doc_ids).intersection(rel_set)),
        answer_cites_relevant=bool(set(cited_doc_ids).intersection(rel_set)),
        answer=answer,
        usage=usage,
    )


async def _run_rag_report_probe(
    *,
    name: str,
    retriever: str,
    query_id: str,
    query: str,
    retrieval_limit: int,
    evidence_limit: int,
    evidence_chars: int,
    max_tokens: int,
    profile: str,
    entry_to_doc: Mapping[str, str],
    relevant_doc_ids: list[str],
    expected_labels: list[str],
    timeout_seconds: float,
    started: float,
    ranked_entries: list[str],
    evidence_entry_ids: list[str],
) -> dict[str, Any]:
    side_started = time.monotonic()
    async with session_scope() as session:
        evidence_entry_ids = [
            eid for eid in evidence_entry_ids if eid in entry_to_doc
        ][:evidence_limit]
        evidence = await _read_answer_evidence(
            session,
            query=query,
            entry_ids=evidence_entry_ids,
            entry_to_doc=entry_to_doc,
            evidence_chars=evidence_chars,
        )

    ranked_doc_ids = [
        entry_to_doc[eid]
        for eid in ranked_entries
        if eid in entry_to_doc
    ]
    evidence_doc_ids = [
        entry_to_doc[eid]
        for eid in evidence_entry_ids
        if eid in entry_to_doc
    ]
    rel_set = set(relevant_doc_ids)
    first_relevant_rank = next(
        (idx + 1 for idx, doc_id in enumerate(ranked_doc_ids) if doc_id in rel_set),
        None,
    )
    answer, usage = await _complete_report_probe(
        query=query,
        evidence=evidence,
        profile=profile,
        max_tokens=max_tokens,
    )
    return _report_side_result(
        kind="rag",
        name=name,
        retriever=retriever,
        query_id=query_id,
        query=query,
        timeout_seconds=timeout_seconds,
        elapsed_ms=int((time.monotonic() - side_started) * 1000),
        total_elapsed_ms=int((time.monotonic() - started) * 1000),
        retrieval_limit=retrieval_limit,
        evidence_limit=evidence_limit,
        relevant_doc_ids=relevant_doc_ids,
        ranked_doc_ids=ranked_doc_ids,
        evidence_doc_ids=evidence_doc_ids,
        answer=answer,
        usage=usage,
        entry_to_doc=entry_to_doc,
        expected_labels=expected_labels,
        first_relevant_rank=first_relevant_rank,
    )


async def _run_react_report_probe(
    *,
    name: str,
    query_id: str,
    query: str,
    profile: str,
    entry_to_doc: Mapping[str, str],
    relevant_doc_ids: list[str],
    expected_labels: list[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    side_started = time.monotonic()
    session_id = ""
    conversation_id: str | None = None
    answer = ""
    answer_event = ""
    done_payload: dict[str, Any] = {}
    tool_names: list[str] = []

    async with session_scope() as session:
        row = await sessions_repo.create_session(
            session,
            initiating_user_message=_truncate(query, 160),
        )
        await session.commit()
        session_id = row.id

    try:
        async for event in run_turn(
            session_id=session_id,
            user_message=_render_react_report_user_prompt(query),
        ):
            if event.event_type == "conversation":
                conversation_id = event.data
            elif event.event_type == "answer":
                answer_event = event.data or answer_event
            elif event.event_type == "tool_call":
                payload = _parse_json_object(event.data)
                name_value = payload.get("name") if isinstance(payload, Mapping) else None
                if name_value:
                    tool_names.append(str(name_value))
            elif event.event_type == "done":
                payload = _parse_json_object(event.data)
                if isinstance(payload, dict):
                    done_payload = payload
    finally:
        async with session_scope() as session:
            if conversation_id:
                conv = await sessions_repo.get_conversation(session, conversation_id)
                if conv is not None and conv.agent_response:
                    answer = conv.agent_response
            if session_id:
                await sessions_repo.close_session(
                    session,
                    session_id=session_id,
                    end_reason="normal",
                )
            await session.commit()

    if not answer:
        answer = answer_event
    usage = {
        "input_tokens": int(done_payload.get("tokens_in") or 0),
        "output_tokens": int(done_payload.get("tokens_out") or 0),
        "cache_read_tokens": int(done_payload.get("cache_read") or 0),
        "cache_creation_tokens": int(done_payload.get("cache_creation_tokens") or 0),
    }
    side = _report_side_result(
        kind="react",
        name=name,
        retriever="react",
        query_id=query_id,
        query=query,
        timeout_seconds=timeout_seconds,
        elapsed_ms=int((time.monotonic() - side_started) * 1000),
        total_elapsed_ms=int((time.monotonic() - side_started) * 1000),
        retrieval_limit=0,
        evidence_limit=0,
        relevant_doc_ids=relevant_doc_ids,
        ranked_doc_ids=[],
        evidence_doc_ids=[],
        answer=answer,
        usage=usage,
        entry_to_doc=entry_to_doc,
        expected_labels=expected_labels,
        first_relevant_rank=None,
    )
    side.update({
        "session_id": session_id,
        "conversation_id": conversation_id,
        "tool_names": tool_names,
        "tool_calls": int(done_payload.get("tool_calls") or len(tool_names)),
        "llm_calls": int(done_payload.get("llm_calls") or 0),
        "duration_ms": int(done_payload.get("duration_ms") or side["elapsed_ms"]),
        "truncated": bool(done_payload.get("truncated")),
        "runtime_profile": "chat",
        "requested_profile": profile,
    })
    return side


def _report_side_result(
    *,
    kind: str,
    name: str,
    retriever: str,
    query_id: str,
    query: str,
    timeout_seconds: float,
    elapsed_ms: int,
    total_elapsed_ms: int,
    retrieval_limit: int,
    evidence_limit: int,
    relevant_doc_ids: list[str],
    ranked_doc_ids: list[str],
    evidence_doc_ids: list[str],
    answer: str,
    usage: dict[str, int],
    entry_to_doc: Mapping[str, str],
    expected_labels: list[str],
    first_relevant_rank: int | None,
) -> dict[str, Any]:
    rel_set = set(relevant_doc_ids)
    cited_entry_ids = _extract_cited_entry_ids(
        answer,
        known_entry_ids=entry_to_doc.keys(),
    )
    cited_doc_ids = [
        entry_to_doc[eid]
        for eid in cited_entry_ids
        if eid in entry_to_doc
    ]
    predicted_label = _predict_answer_label(answer)
    label_correct = (
        predicted_label in set(expected_labels)
        if expected_labels and predicted_label is not None
        else (False if expected_labels else None)
    )
    return {
        "kind": kind,
        "name": name,
        "retriever": retriever,
        "query_id": query_id,
        "query": query,
        "timed_out": False,
        "timeout_seconds": timeout_seconds,
        "elapsed_ms": elapsed_ms,
        "total_elapsed_ms": total_elapsed_ms,
        "retrieval_limit": retrieval_limit,
        "evidence_limit": evidence_limit,
        "relevant_doc_ids": relevant_doc_ids,
        "ranked_doc_ids": ranked_doc_ids,
        "evidence_doc_ids": evidence_doc_ids,
        "cited_entry_ids": cited_entry_ids,
        "cited_doc_ids": cited_doc_ids,
        "expected_labels": expected_labels,
        "predicted_label": predicted_label,
        "label_correct": label_correct,
        "first_relevant_rank": first_relevant_rank,
        "evidence_contains_relevant": bool(set(evidence_doc_ids).intersection(rel_set)),
        "answer_cites_relevant": bool(set(cited_doc_ids).intersection(rel_set)),
        "answer": answer,
        "usage": usage,
        "error": None,
    }


def _empty_report_side(kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "timed_out": True,
        "timeout_seconds": 0.0,
        "elapsed_ms": 0,
        "total_elapsed_ms": 0,
        "retrieval_limit": 0,
        "evidence_limit": 0,
        "relevant_doc_ids": [],
        "ranked_doc_ids": [],
        "evidence_doc_ids": [],
        "cited_entry_ids": [],
        "cited_doc_ids": [],
        "expected_labels": [],
        "predicted_label": None,
        "label_correct": None,
        "first_relevant_rank": None,
        "evidence_contains_relevant": False,
        "answer_cites_relevant": False,
        "answer": "",
        "usage": {},
        "error": "empty",
    }


async def _read_answer_evidence(
    session: AsyncSession,
    *,
    query: str,
    entry_ids: list[str],
    entry_to_doc: Mapping[str, str],
    evidence_chars: int,
) -> list[dict[str, Any]]:
    if not entry_ids:
        return []
    ctx = ToolContext(session_id="eval-answer", conversation_id="eval-answer")
    metadata = await read_entries_metadata(
        session,
        ctx,
        {"entry_ids": entry_ids, "related_limit": 0},
    )
    metadata_by_id = {
        str(item.get("entry_id")): item
        for item in metadata.get("entries") or []
        if item.get("entry_id")
    }
    patterns = _query_patterns(query)
    read_specs: list[dict[str, Any]] = []
    if patterns:
        read_specs.append({
            "patterns": patterns,
            "context_lines": 2,
            "max_matches": 3,
        })
    read_specs.append({"offset": 0, "max_chars": evidence_chars})
    reads = await read_files(
        session,
        ctx,
        {
            "requests": [
                {
                    "entry_id": eid,
                    "reads": read_specs,
                }
                for eid in entry_ids
            ],
        },
    )
    reads_by_id = {
        str(item.get("entry_id")): item
        for item in reads.get("results") or []
        if item.get("entry_id")
    }

    evidence: list[dict[str, Any]] = []
    for rank, entry_id in enumerate(entry_ids, start=1):
        meta = metadata_by_id.get(entry_id) or {}
        read = reads_by_id.get(entry_id) or {}
        file_meta = meta.get("file") if isinstance(meta.get("file"), dict) else {}
        text = _collect_read_text(read, max_chars=evidence_chars)
        evidence.append({
            "rank": rank,
            "entry_id": entry_id,
            "doc_id": entry_to_doc.get(entry_id),
            "display_name": meta.get("display_name") or entry_id,
            "summary": file_meta.get("summary") or "",
            "description": _compact_description(file_meta.get("description")),
            "text": text,
            "read_ok": bool(text),
            "read_error": read.get("error"),
        })
    return evidence
