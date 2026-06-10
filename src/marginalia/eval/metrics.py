"""Retrieval metric calculations for eval runs."""
from __future__ import annotations

import math
from typing import Any, Mapping

from marginalia.eval.types import EvalRunResult

def _score_query(
    ranked_doc_ids: list[str],
    relevant: Mapping[str, int],
    ks: list[int],
) -> dict[str, Any]:
    rel_set = set(relevant)
    first_rank = next(
        (idx + 1 for idx, doc_id in enumerate(ranked_doc_ids) if doc_id in rel_set),
        None,
    )
    hit: dict[str, float] = {}
    recall: dict[str, float] = {}
    ndcg: dict[str, float] = {}
    for k in ks:
        top = ranked_doc_ids[:k]
        hits = len(set(top).intersection(rel_set))
        hit[str(k)] = 1.0 if hits else 0.0
        recall[str(k)] = hits / len(rel_set)
        ndcg[str(k)] = _ndcg_at_k(top, relevant, k)
    return {
        "first_relevant_rank": first_rank,
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "hit": hit,
        "recall": recall,
        "ndcg": ndcg,
    }


def _ndcg_at_k(
    ranked_doc_ids: list[str],
    relevant: Mapping[str, int],
    k: int,
) -> float:
    def gain(rel: int) -> float:
        return (2.0 ** rel) - 1.0

    dcg = 0.0
    for idx, doc_id in enumerate(ranked_doc_ids[:k], start=1):
        rel = relevant.get(doc_id, 0)
        if rel <= 0:
            continue
        dcg += gain(rel) / math.log2(idx + 1)
    ideal = sorted((rel for rel in relevant.values() if rel > 0), reverse=True)[:k]
    idcg = sum(gain(rel) / math.log2(idx + 1) for idx, rel in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0


class _MetricAccumulator:
    def __init__(self, ks: list[int]) -> None:
        self.ks = ks
        self.evaluated = 0
        self.skipped = 0
        self.zero_results = 0
        self.no_relevant_at_max_k = 0
        self.mrr = 0.0
        self.hit_rate = {k: 0.0 for k in ks}
        self.recall = {k: 0.0 for k in ks}
        self.ndcg = {k: 0.0 for k in ks}

    def add(self, scored: Mapping[str, Any], *, zero_result: bool) -> None:
        self.evaluated += 1
        if zero_result:
            self.zero_results += 1
        if not scored.get("first_relevant_rank"):
            self.no_relevant_at_max_k += 1
        self.mrr += float(scored.get("mrr") or 0.0)
        scored_hit = scored.get("hit") or {}
        scored_recall = scored.get("recall") or {}
        scored_ndcg = scored.get("ndcg") or {}
        for k in self.ks:
            self.hit_rate[k] += float(scored_hit.get(str(k)) or 0.0)
            self.recall[k] += float(scored_recall.get(str(k)) or 0.0)
            self.ndcg[k] += float(scored_ndcg.get(str(k)) or 0.0)

    def result(
        self,
        *,
        name: str,
        retriever: str,
        queries_total: int,
        per_query: list[dict[str, Any]],
    ) -> EvalRunResult:
        denom = max(1, self.evaluated)
        return EvalRunResult(
            name=name,
            retriever=retriever,
            queries_total=queries_total,
            queries_evaluated=self.evaluated,
            queries_skipped=self.skipped,
            zero_result_rate=self.zero_results / denom,
            no_relevant_at_k_rate=self.no_relevant_at_max_k / denom,
            mrr=self.mrr / denom,
            hit_rate={k: self.hit_rate[k] / denom for k in self.ks},
            recall={k: self.recall[k] / denom for k in self.ks},
            ndcg={k: self.ndcg[k] / denom for k in self.ks},
            per_query=per_query,
        )
