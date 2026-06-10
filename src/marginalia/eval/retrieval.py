"""Retrieval eval runners and retrieval component ablations."""
from __future__ import annotations

import asyncio
from typing import Any, Iterable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from marginalia.agent.text_query import normalize_text_queries
from marginalia.agent.tools import ToolContext
from marginalia.agent.tools.recall_knowledge import (
    load_rerank_documents_by_entry_id,
    recall_knowledge,
    rerank_recall_entries_with_documents,
    score_recall_entries,
    select_evidence_entry_ids,
)
from marginalia.agent.tools.search_metadata import search_metadata
from marginalia.config import get_settings
from marginalia.db.bootstrap import bootstrap_schema
from marginalia.db.session import session_scope
from marginalia.eval.datasets import eval_root, iter_beir_queries, load_qrels
from marginalia.eval.metrics import _MetricAccumulator, _score_query
from marginalia.eval.reporting import result_to_dict
from marginalia.eval.types import BeirQuery, EvalAblationConfig, EvalAblationRunResult, EvalRunResult
from marginalia.eval.utils import _append_unique_str, _read_json
from marginalia.repositories import entries as entries_repo
from marginalia.repositories import entry_relations as relations_repo
from marginalia.semantic.index import (
    semantic_entry_rows,
    semantic_recall_configured,
    search_semantic_index_many,
)
from marginalia.semantic.rerank import rerank_configured

async def run_eval_dataset(
    *,
    name: str,
    retriever: str = "search_metadata",
    k_values: Iterable[int] = (10, 50, 100),
    query_limit: int | None = None,
    semantic_recall: bool | None = None,
    rerank: bool | None = None,
    relation_expansion: bool | None = None,
) -> EvalRunResult:
    """Run retrieval evaluation against an already-imported eval dataset."""
    await bootstrap_schema()
    dataset_dir = eval_root() / name
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"eval dataset {name!r} is not imported")
    doc_map: dict[str, str] = _read_json(dataset_dir / "doc_map.json")
    entry_to_doc = {entry_id: doc_id for doc_id, entry_id in doc_map.items()}
    queries = list(iter_beir_queries(dataset_dir / "queries.jsonl"))
    if query_limit is not None:
        queries = queries[:query_limit]
    qrels = load_qrels(dataset_dir / "qrels.tsv")

    ks = sorted({int(k) for k in k_values if int(k) > 0})
    if not ks:
        ks = [10]
    max_k = max(ks)

    per_query: list[dict[str, Any]] = []
    aggregate = _MetricAccumulator(ks)
    if retriever == "semantic_recall":
        eligible: list[tuple[BeirQuery, dict[str, int]]] = []
        for q in queries:
            relevant = {
                doc_id: rel
                for doc_id, rel in qrels.get(q.query_id, {}).items()
                if doc_id in doc_map and rel > 0
            }
            if not relevant:
                aggregate.skipped += 1
                continue
            eligible.append((q, relevant))
        batched_hits = await search_semantic_index_many(
            [q.text for q, _relevant in eligible],
            limit=max_k,
        )
        for (q, relevant), hits in zip(eligible, batched_hits):
            ranked_entries = [
                hit.entry_id
                for hit in hits
                if hit.entry_id in entry_to_doc
            ]
            if relation_expansion:
                async with session_scope() as session:
                    ranked_entries = await _maybe_expand_ranked_ids(
                        session,
                        ranked_entries,
                        limit=max_k,
                        enabled=True,
                    )
            ranked_docs = [
                entry_to_doc[eid]
                for eid in ranked_entries
                if eid in entry_to_doc
            ]
            scored = _score_query(ranked_docs, relevant, ks)
            aggregate.add(scored, zero_result=not ranked_docs)
            per_query.append({
                "query_id": q.query_id,
                "query": q.text,
                "relevant_doc_ids": sorted(relevant),
                "ranked_doc_ids": ranked_docs,
                **scored,
            })
        return aggregate.result(
            name=name,
            retriever=retriever,
            queries_total=len(queries),
            per_query=per_query,
        )

    async with session_scope() as session:
        if retriever == "recall_knowledge":
            eligible = []
            for q in queries:
                relevant = {
                    doc_id: rel
                    for doc_id, rel in qrels.get(q.query_id, {}).items()
                    if doc_id in doc_map and rel > 0
                }
                if not relevant:
                    aggregate.skipped += 1
                    continue
                eligible.append((q, relevant))
            ranked_many = await _retrieve_entries_many(
                session,
                retriever=retriever,
                queries=[q.text for q, _relevant in eligible],
                limit=max_k,
                semantic_recall=semantic_recall,
                rerank=rerank,
                relation_expansion=relation_expansion,
            )
            for (q, relevant), ranked_entries in zip(eligible, ranked_many):
                ranked_docs = [
                    entry_to_doc[eid]
                    for eid in ranked_entries
                    if eid in entry_to_doc
                ]
                scored = _score_query(ranked_docs, relevant, ks)
                aggregate.add(scored, zero_result=not ranked_docs)
                per_query.append({
                    "query_id": q.query_id,
                    "query": q.text,
                    "relevant_doc_ids": sorted(relevant),
                    "ranked_doc_ids": ranked_docs,
                    **scored,
                })
            return aggregate.result(
                name=name,
                retriever=retriever,
                queries_total=len(queries),
                per_query=per_query,
            )

        for q in queries:
            relevant = {
                doc_id: rel
                for doc_id, rel in qrels.get(q.query_id, {}).items()
                if doc_id in doc_map and rel > 0
            }
            if not relevant:
                aggregate.skipped += 1
                continue
            ranked_entries = await _retrieve_entries(
                session,
                retriever=retriever,
                query=q.text,
                limit=max_k,
                relation_expansion=relation_expansion,
            )
            ranked_docs = [
                entry_to_doc[eid]
                for eid in ranked_entries
                if eid in entry_to_doc
            ]
            scored = _score_query(ranked_docs, relevant, ks)
            aggregate.add(scored, zero_result=not ranked_docs)
            per_query.append({
                "query_id": q.query_id,
                "query": q.text,
                "relevant_doc_ids": sorted(relevant),
                "ranked_doc_ids": ranked_docs,
                **scored,
            })

    return aggregate.result(
        name=name,
        retriever=retriever,
        queries_total=len(queries),
        per_query=per_query,
    )


def default_ablation_configs() -> list[EvalAblationConfig]:
    return [
        EvalAblationConfig(
            name="metadata_only",
            retriever="search_metadata",
        ),
        EvalAblationConfig(
            name="metadata_plus_relations",
            retriever="search_metadata",
            relation_expansion=True,
        ),
        EvalAblationConfig(
            name="hybrid_no_rerank",
            retriever="recall_knowledge",
            semantic_recall=True,
        ),
        EvalAblationConfig(
            name="hybrid_plus_relations",
            retriever="recall_knowledge",
            semantic_recall=True,
            relation_expansion=True,
        ),
        EvalAblationConfig(
            name="hybrid_plus_rerank",
            retriever="recall_knowledge",
            semantic_recall=True,
            rerank=True,
        ),
        EvalAblationConfig(
            name="full_recall",
            retriever="recall_knowledge",
            semantic_recall=True,
            rerank=True,
            relation_expansion=True,
        ),
    ]


async def run_eval_ablation_dataset(
    *,
    name: str,
    k_values: Iterable[int] = (10, 50, 100),
    query_limit: int | None = None,
    configs: Iterable[EvalAblationConfig] | None = None,
) -> EvalAblationRunResult:
    ks = sorted({int(k) for k in k_values if int(k) > 0}) or [10]
    run_configs = list(configs or default_ablation_configs())
    runs: list[dict[str, Any]] = []
    baseline: EvalRunResult | None = None
    for config in run_configs:
        result = await run_eval_dataset(
            name=name,
            retriever=config.retriever,
            k_values=ks,
            query_limit=query_limit,
            semantic_recall=config.semantic_recall,
            rerank=config.rerank,
            relation_expansion=config.relation_expansion,
        )
        if baseline is None:
            baseline = result
        runs.append({
            "config": _ablation_config_to_dict(config),
            "delta_vs_baseline": _ablation_delta(result, baseline, max(ks)),
            "result": result_to_dict(result),
        })
    return EvalAblationRunResult(
        name=name,
        k_values=ks,
        query_limit=query_limit,
        runs=runs,
    )


def _ablation_config_to_dict(config: EvalAblationConfig) -> dict[str, Any]:
    return {
        "name": config.name,
        "retriever": config.retriever,
        "plan_phase": config.plan_phase,
        "semantic_recall": config.semantic_recall,
        "rerank": config.rerank,
        "relation_expansion": config.relation_expansion,
    }


def _ablation_delta(
    result: EvalRunResult,
    baseline: EvalRunResult,
    max_k: int,
) -> dict[str, float]:
    return {
        "mrr": result.mrr - baseline.mrr,
        f"hit@{max_k}": result.hit_rate.get(max_k, 0.0)
        - baseline.hit_rate.get(max_k, 0.0),
        f"candidate_recall@{max_k}": result.recall.get(max_k, 0.0)
        - baseline.recall.get(max_k, 0.0),
        f"ndcg@{max_k}": result.ndcg.get(max_k, 0.0)
        - baseline.ndcg.get(max_k, 0.0),
    }


async def _retrieve_entries(
    session: AsyncSession,
    *,
    retriever: str,
    query: str,
    limit: int,
    relation_expansion: bool | None = None,
) -> list[str]:
    ctx = ToolContext(session_id="eval", conversation_id="eval")
    if retriever == "search_metadata":
        result = await search_metadata(
            session,
            ctx,
            {"text": query, "limit": limit},
        )
        ranked = [str(e["entry_id"]) for e in result.get("entries") or []]
        return await _maybe_expand_ranked_ids(
            session,
            ranked,
            limit=limit,
            enabled=bool(relation_expansion),
        )
    if retriever == "semantic_recall":
        rows = await semantic_entry_rows(session, query, limit=limit)
        ranked = [str(e["entry_id"]) for e in rows]
        return await _maybe_expand_ranked_ids(
            session,
            ranked,
            limit=limit,
            enabled=bool(relation_expansion),
        )
    if retriever == "recall_knowledge":
        result = await recall_knowledge(
            session,
            ctx,
            {"text": query, "limit": limit},
        )
        return [str(eid) for eid in result.get("verify_entry_ids") or []]
    raise ValueError(
        "unknown retriever "
        f"{retriever!r}; expected search_metadata, semantic_recall, or recall_knowledge"
    )


async def _retrieve_entries_many(
    session: AsyncSession,
    *,
    retriever: str,
    queries: list[str],
    limit: int,
    semantic_recall: bool | None = None,
    rerank: bool | None = None,
    relation_expansion: bool | None = None,
) -> list[list[str]]:
    details = await _retrieve_entries_many_detail(
        session,
        retriever=retriever,
        queries=queries,
        limit=limit,
        evidence_limit=None,
        semantic_recall=semantic_recall,
        rerank=rerank,
        relation_expansion=relation_expansion,
    )
    return [detail["ranked_ids"] for detail in details]


async def _retrieve_entries_many_detail(
    session: AsyncSession,
    *,
    retriever: str,
    queries: list[str],
    limit: int,
    evidence_limit: int | None,
    semantic_recall: bool | None = None,
    rerank: bool | None = None,
    relation_expansion: bool | None = None,
) -> list[dict[str, list[str]]]:
    if not queries:
        return []
    if retriever == "recall_knowledge":
        return await _retrieve_recall_knowledge_many(
            session,
            queries=queries,
            limit=limit,
            evidence_limit=evidence_limit,
            semantic_recall=semantic_recall,
            rerank=rerank,
            relation_expansion=relation_expansion,
        )
    ranked_many = [
        await _retrieve_entries(
            session,
            retriever=retriever,
            query=query,
            limit=limit,
            relation_expansion=relation_expansion,
        )
        for query in queries
    ]
    return [
        {
            "ranked_ids": ranked,
            "evidence_ids": ranked[:evidence_limit] if evidence_limit else ranked,
        }
        for ranked in ranked_many
    ]


async def _retrieve_recall_knowledge_many(
    session: AsyncSession,
    *,
    queries: list[str],
    limit: int,
    evidence_limit: int | None,
    semantic_recall: bool | None = None,
    rerank: bool | None = None,
    relation_expansion: bool | None = None,
) -> list[dict[str, list[str]]]:
    fetch_limit = 100
    settings = get_settings()
    use_semantic = (
        semantic_recall_configured()
        if semantic_recall is None
        else bool(semantic_recall) and semantic_recall_configured()
    )
    use_rerank = (
        rerank_configured(settings)
        if rerank is None
        else bool(rerank) and rerank_configured(settings)
    )
    text_terms_by_query = [normalize_text_queries(query) for query in queries]
    semantic_queries = [" ".join(terms) for terms in text_terms_by_query]
    semantic_hits_many = (
        await search_semantic_index_many(
            semantic_queries,
            limit=min(fetch_limit, settings.semantic_recall_limit),
        )
        if use_semantic
        else [[] for _query in queries]
    )
    semantic_ids = sorted({
        hit.entry_id
        for hits in semantic_hits_many
        for hit in hits
    })
    semantic_rows_by_id = await _entry_rows_by_id(session, semantic_ids)
    metadata_results = await _search_metadata_many(
        text_terms_by_query,
        limit=fetch_limit,
        concurrency=20,
    )

    ranked_by_query: list[list[dict[str, Any]]] = []
    queries_for_rerank: list[str] = []
    rerank_entry_ids: list[str] = []
    rerank_top_n = max(1, int(settings.rerank_top_n or 80))
    for text_terms, metadata_entries, semantic_hits in zip(
        text_terms_by_query,
        metadata_results,
        semantic_hits_many,
    ):
        entry_map: dict[str, dict[str, Any]] = {}
        _merge_eval_entries(entry_map, metadata_entries, "metadata_text")
        semantic_entries = [
            semantic_rows_by_id[hit.entry_id]
            for hit in semantic_hits
            if hit.entry_id in semantic_rows_by_id
        ]
        _merge_eval_entries(entry_map, semantic_entries, "semantic")
        ranked = score_recall_entries(list(entry_map.values()), text_terms=text_terms)
        ranked_by_query.append(ranked)
        queries_for_rerank.append(" ".join(text_terms))
        if text_terms and use_rerank:
            for row in ranked[:rerank_top_n]:
                entry_id = str(row.get("entry_id") or "")
                if entry_id:
                    rerank_entry_ids.append(entry_id)

    if rerank_entry_ids and use_rerank:
        documents_by_id = await load_rerank_documents_by_entry_id(session, rerank_entry_ids)
        semaphore = asyncio.Semaphore(max(1, int(settings.rerank_concurrency or 10)))

        async def _rerank_one(
            query: str,
            ranked: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            if not query.strip() or not ranked:
                return ranked
            async with semaphore:
                reranked, _trace = await rerank_recall_entries_with_documents(
                    ranked,
                    query=query,
                    documents_by_id=documents_by_id,
                )
                return reranked

        ranked_by_query = await asyncio.gather(*(
            _rerank_one(query, ranked)
            for query, ranked in zip(queries_for_rerank, ranked_by_query)
        ))

    out: list[dict[str, list[str]]] = []
    for ranked in ranked_by_query:
        ranked_ids = [
            str(entry.get("entry_id"))
            for entry in ranked[:max(1, limit)]
            if entry.get("entry_id")
        ]
        ranked_ids = await _maybe_expand_ranked_ids(
            session,
            ranked_ids,
            limit=limit,
            enabled=bool(relation_expansion),
        )
        evidence_ids = (
            _expand_evidence_ids(
                ranked_ids=ranked_ids,
                evidence_ids=select_evidence_entry_ids(
                    ranked[:max(1, limit)],
                    max(1, evidence_limit),
                ),
                limit=max(1, evidence_limit),
            )
            if evidence_limit
            else ranked_ids
        )
        out.append({"ranked_ids": ranked_ids, "evidence_ids": evidence_ids})
    return out


async def _maybe_expand_ranked_ids(
    session: AsyncSession,
    ranked_ids: list[str],
    *,
    limit: int,
    enabled: bool,
) -> list[str]:
    if not enabled or not ranked_ids:
        return ranked_ids[:max(1, limit)]
    out: list[str] = []
    seen: set[str] = set()
    for entry_id in ranked_ids:
        if entry_id and entry_id not in seen:
            seen.add(entry_id)
            out.append(entry_id)
        if len(out) >= limit:
            return out

    per_anchor_limit = max(1, min(10, limit))
    for anchor_id in ranked_ids[:max(1, limit)]:
        rel_rows = await relations_repo.list_top_for_entry(
            session,
            anchor_id,
            limit=per_anchor_limit,
            vetted_only=True,
        )
        for relation in rel_rows:
            other_id = (
                relation.entry_b_id
                if relation.entry_a_id == anchor_id
                else relation.entry_a_id
            )
            if not other_id or other_id in seen:
                continue
            seen.add(other_id)
            out.append(other_id)
            if len(out) >= limit:
                return out
    return out[:max(1, limit)]


def _expand_evidence_ids(
    *,
    ranked_ids: list[str],
    evidence_ids: list[str],
    limit: int,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for entry_id in evidence_ids + ranked_ids:
        if not entry_id or entry_id in seen:
            continue
        seen.add(entry_id)
        out.append(entry_id)
        if len(out) >= limit:
            return out
    return out


async def _search_metadata_many(
    text_terms_by_query: list[list[str]],
    *,
    limit: int,
    concurrency: int,
) -> list[list[Any]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(text_terms: list[str]) -> list[Any]:
        if not text_terms:
            return []
        async with semaphore:
            async with session_scope() as session:
                result = await search_metadata(
                    session,
                    ToolContext(session_id="eval", conversation_id="eval"),
                    {"text": text_terms, "limit": limit},
                )
                return list(result.get("entries") or [])

    return await asyncio.gather(*(_one(text_terms) for text_terms in text_terms_by_query))


async def _entry_rows_by_id(
    session: AsyncSession,
    entry_ids: list[str],
) -> dict[str, dict[str, Any]]:
    rows = await entries_repo.list_live_with_file_by_ids(session, entry_ids)
    out: dict[str, dict[str, Any]] = {}
    for entry, file_row in rows:
        out[entry.id] = {
            "entry_id": entry.id,
            "display_name": entry.display_name,
            "lifecycle": entry.lifecycle,
            "kind": file_row.kind,
            "summary": file_row.summary,
            "catalog_id": entry.catalog_id,
            "folder_id": entry.folder_id,
        }
    return out


def _merge_eval_entries(
    entry_map: dict[str, dict[str, Any]],
    entries: list[Any],
    source: str,
) -> None:
    total = len(entries)
    for idx, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        entry_id = str(entry.get("entry_id") or "")
        if not entry_id:
            continue
        existing = entry_map.get(entry_id)
        if existing is None:
            existing = {
                "entry_id": entry_id,
                "display_name": entry.get("display_name"),
                "lifecycle": entry.get("lifecycle"),
                "kind": entry.get("kind"),
                "summary": entry.get("summary"),
                "catalog_id": entry.get("catalog_id"),
                "folder_id": entry.get("folder_id"),
                "coverage": entry.get("coverage"),
                "matched_by": [],
                "rrf_score": 0.0,
                "rank_score": 0,
                "score": 0.0,
                "score_components": {},
            }
            entry_map[entry_id] = existing
        else:
            for key in (
                "display_name",
                "lifecycle",
                "kind",
                "summary",
                "catalog_id",
                "folder_id",
                "coverage",
            ):
                if existing.get(key) in (None, "") and entry.get(key) not in (None, ""):
                    existing[key] = entry.get(key)
        _append_unique_str(existing["matched_by"], source)
        rank_key = _rank_key_for_source(source)
        if rank_key:
            rank = idx + 1
            existing[rank_key] = min(
                int(existing.get(rank_key) or rank),
                rank,
            )
        existing["rank_score"] = max(
            int(existing.get("rank_score") or 0),
            total - idx,
        )
        existing["rrf_score"] = _eval_rrf_score(existing)


def _rank_key_for_source(source: str) -> str | None:
    if source in {"metadata_text", "metadata_tags"}:
        return "lexical_rank"
    if source == "semantic":
        return "semantic_rank"
    return None


def _eval_rrf_score(row: Mapping[str, Any], *, k: int = 60) -> float:
    score = 0.0
    for key in ("lexical_rank", "semantic_rank"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            rank = int(raw)
        except (TypeError, ValueError):
            continue
        if rank > 0:
            score += 1.0 / (k + rank)
    return score


def _eval_entry_sort_key(row: Mapping[str, Any]) -> tuple[float, int, int, str]:
    matched_by = set(row.get("matched_by") or [])
    return (
        -float(row.get("rrf_score") or 0.0),
        -int("metadata_text" in matched_by and "semantic" in matched_by),
        -int(row.get("rank_score") or 0),
        str(row.get("display_name") or ""),
    )


def _select_quota_evidence_ids(
    ranked: list[Mapping[str, Any]],
    evidence_limit: int,
) -> list[str]:
    if evidence_limit <= 0:
        return []
    overlap_quota, lexical_quota, semantic_quota = _evidence_quotas(evidence_limit)
    overlap: list[Mapping[str, Any]] = []
    lexical_only: list[Mapping[str, Any]] = []
    semantic_only: list[Mapping[str, Any]] = []
    for row in ranked:
        matched_by = set(row.get("matched_by") or [])
        has_lexical = "metadata_text" in matched_by or "metadata_tags" in matched_by
        has_semantic = "semantic" in matched_by
        if has_lexical and has_semantic:
            overlap.append(row)
        elif has_lexical:
            lexical_only.append(row)
        elif has_semantic:
            semantic_only.append(row)

    out: list[str] = []
    seen: set[str] = set()

    def take(rows: list[Mapping[str, Any]], quota: int) -> None:
        for row in rows:
            if len(out) >= evidence_limit or quota <= 0:
                return
            entry_id = str(row.get("entry_id") or "")
            if not entry_id or entry_id in seen:
                continue
            seen.add(entry_id)
            out.append(entry_id)
            quota -= 1

    take(overlap, overlap_quota)
    take(lexical_only, lexical_quota)
    take(semantic_only, semantic_quota)
    take(ranked, evidence_limit - len(out))
    return out[:evidence_limit]


def _evidence_quotas(evidence_limit: int) -> tuple[int, int, int]:
    if evidence_limit <= 1:
        return evidence_limit, 0, 0
    overlap = max(1, round(evidence_limit * 0.4))
    lexical = max(1, round(evidence_limit * 0.4))
    semantic = max(0, evidence_limit - overlap - lexical)
    return overlap, lexical, semantic
