"""Eval result serialization, aggregation, and text formatting."""
from __future__ import annotations

from typing import Any, Mapping

from marginalia.eval.types import (
    EvalAblationRunResult,
    EvalAnswerProbeResult,
    EvalAnswerRunResult,
    EvalReportCompareResult,
    EvalRunResult,
)

def result_to_dict(result: EvalRunResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "retriever": result.retriever,
        "queries_total": result.queries_total,
        "queries_evaluated": result.queries_evaluated,
        "queries_skipped": result.queries_skipped,
        "zero_result_rate": result.zero_result_rate,
        "no_relevant_at_k_rate": result.no_relevant_at_k_rate,
        "mrr": result.mrr,
        "hit_rate": {str(k): v for k, v in result.hit_rate.items()},
        "recall": {str(k): v for k, v in result.recall.items()},
        "ndcg": {str(k): v for k, v in result.ndcg.items()},
        "per_query": result.per_query,
    }


def ablation_run_to_dict(result: EvalAblationRunResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "k_values": result.k_values,
        "query_limit": result.query_limit,
        "runs": result.runs,
    }


def answer_probe_to_dict(result: EvalAnswerProbeResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "retriever": result.retriever,
        "query_id": result.query_id,
        "query": result.query,
        "timed_out": result.timed_out,
        "timeout_seconds": result.timeout_seconds,
        "elapsed_ms": result.elapsed_ms,
        "retrieval_limit": result.retrieval_limit,
        "evidence_limit": result.evidence_limit,
        "relevant_doc_ids": result.relevant_doc_ids,
        "ranked_doc_ids": result.ranked_doc_ids,
        "evidence_doc_ids": result.evidence_doc_ids,
        "cited_entry_ids": result.cited_entry_ids,
        "cited_doc_ids": result.cited_doc_ids,
        "expected_labels": result.expected_labels,
        "predicted_label": result.predicted_label,
        "label_correct": result.label_correct,
        "first_relevant_rank": result.first_relevant_rank,
        "evidence_contains_relevant": result.evidence_contains_relevant,
        "answer_cites_relevant": result.answer_cites_relevant,
        "answer": result.answer,
        "usage": result.usage,
        "error": result.error,
    }


def answer_run_to_dict(result: EvalAnswerRunResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "retriever": result.retriever,
        "queries_total": result.queries_total,
        "queries_evaluated": result.queries_evaluated,
        "queries_skipped": result.queries_skipped,
        "timed_out": result.timed_out,
        "timeout_seconds": result.timeout_seconds,
        "concurrency": result.concurrency,
        "total_elapsed_ms": result.total_elapsed_ms,
        "answer_citation_hit_rate": result.answer_citation_hit_rate,
        "evidence_hit_rate": result.evidence_hit_rate,
        "no_relevant_evidence_rate": result.no_relevant_evidence_rate,
        "avg_first_relevant_rank": result.avg_first_relevant_rank,
        "labels_evaluated": result.labels_evaluated,
        "label_accuracy": result.label_accuracy,
        "usage": result.usage,
        "per_query": result.per_query,
    }


def report_compare_to_dict(result: EvalReportCompareResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "queries_total": result.queries_total,
        "queries_evaluated": result.queries_evaluated,
        "queries_skipped": result.queries_skipped,
        "timed_out": result.timed_out,
        "timeout_seconds": result.timeout_seconds,
        "concurrency": result.concurrency,
        "total_elapsed_ms": result.total_elapsed_ms,
        "rag_wins": result.rag_wins,
        "react_wins": result.react_wins,
        "ties": result.ties,
        "judge_errors": result.judge_errors,
        "rag_citation_hit_rate": result.rag_citation_hit_rate,
        "react_citation_hit_rate": result.react_citation_hit_rate,
        "rag_label_accuracy": result.rag_label_accuracy,
        "react_label_accuracy": result.react_label_accuracy,
        "avg_react_tool_calls": result.avg_react_tool_calls,
        "avg_react_llm_calls": result.avg_react_llm_calls,
        "usage": result.usage,
        "per_query": result.per_query,
    }


def format_run_result(result: EvalRunResult) -> str:
    lines = [
        f"dataset: {result.name}",
        f"retriever: {result.retriever}",
        (
            f"queries: {result.queries_evaluated} evaluated / "
            f"{result.queries_total} total"
        ),
        f"skipped_no_imported_relevance: {result.queries_skipped}",
        f"zero_result_rate: {result.zero_result_rate:.4f}",
        f"no_relevant@max_k_rate: {result.no_relevant_at_k_rate:.4f}",
        f"MRR: {result.mrr:.4f}",
        "",
        "k\thit\tcandidate_recall\tndcg",
    ]
    for k in sorted(result.recall):
        lines.append(
            f"{k}\t{result.hit_rate[k]:.4f}\t"
            f"{result.recall[k]:.4f}\t{result.ndcg[k]:.4f}"
        )
    return "\n".join(lines)


def format_ablation_run_result(result: EvalAblationRunResult) -> str:
    max_k = max(result.k_values) if result.k_values else 10
    lines = [
        f"dataset: {result.name}",
        f"ablation_runs: {len(result.runs)}",
        "plan_phase is false for retrieval ablations; compare-report covers "
        "one-shot RAG versus full plan/execute reports.",
        "",
        (
            "config\tplan\tsemantic\trerank\trelations\t"
            f"MRR\thit@{max_k}\tcandidate_recall@{max_k}\t"
            f"ndcg@{max_k}\tzero_rate\tdelta_mrr\tdelta_recall@{max_k}"
        ),
    ]
    for row in result.runs:
        config = row["config"]
        run = row["result"]
        delta = row["delta_vs_baseline"]
        hit = (run.get("hit_rate") or {}).get(str(max_k), 0.0)
        recall = (run.get("recall") or {}).get(str(max_k), 0.0)
        ndcg = (run.get("ndcg") or {}).get(str(max_k), 0.0)
        lines.append(
            "\t".join([
                str(config["name"]),
                _format_optional_bool(config["plan_phase"]),
                _format_optional_bool(config["semantic_recall"]),
                _format_optional_bool(config["rerank"]),
                _format_optional_bool(config["relation_expansion"]),
                f"{float(run.get('mrr') or 0.0):.4f}",
                f"{float(hit):.4f}",
                f"{float(recall):.4f}",
                f"{float(ndcg):.4f}",
                f"{float(run.get('zero_result_rate') or 0.0):.4f}",
                f"{float(delta.get('mrr') or 0.0):+.4f}",
                f"{float(delta.get(f'candidate_recall@{max_k}') or 0.0):+.4f}",
            ])
        )
    return "\n".join(lines)


def format_answer_run_result(result: EvalAnswerRunResult) -> str:
    lines = [
        f"dataset: {result.name}",
        f"retriever: {result.retriever}",
        (
            f"queries: {result.queries_evaluated} evaluated / "
            f"{result.queries_total} total"
        ),
        f"skipped_no_imported_relevance: {result.queries_skipped}",
        f"timed_out: {result.timed_out}",
        f"timeout_seconds_per_query: {result.timeout_seconds:.1f}",
        f"concurrency: {result.concurrency}",
        f"total_elapsed_ms: {result.total_elapsed_ms}",
        f"evidence_hit_rate: {result.evidence_hit_rate:.4f}",
        f"no_relevant_evidence_rate: {result.no_relevant_evidence_rate:.4f}",
        f"answer_citation_hit_rate: {result.answer_citation_hit_rate:.4f}",
    ]
    if result.avg_first_relevant_rank is not None:
        lines.append(f"avg_first_relevant_rank: {result.avg_first_relevant_rank:.4f}")
    else:
        lines.append("avg_first_relevant_rank: (none)")
    if result.label_accuracy is not None:
        lines.append(
            f"label_accuracy: {result.label_accuracy:.4f} "
            f"({result.labels_evaluated} labeled)"
        )
    else:
        lines.append("label_accuracy: (no labels)")
    return "\n".join(lines)


def format_report_compare_result(result: EvalReportCompareResult) -> str:
    lines = [
        f"dataset: {result.name}",
        (
            f"queries: {result.queries_evaluated} evaluated / "
            f"{result.queries_total} total"
        ),
        f"skipped_no_imported_relevance: {result.queries_skipped}",
        f"timed_out: {result.timed_out}",
        f"timeout_seconds_per_query: {result.timeout_seconds:.1f}",
        f"concurrency: {result.concurrency}",
        f"total_elapsed_ms: {result.total_elapsed_ms}",
        (
            "judge_wins: "
            f"rag={result.rag_wins} react={result.react_wins} ties={result.ties}"
        ),
        f"judge_errors: {result.judge_errors}",
        f"rag_citation_hit_rate: {result.rag_citation_hit_rate:.4f}",
        f"react_citation_hit_rate: {result.react_citation_hit_rate:.4f}",
    ]
    if result.rag_label_accuracy is not None:
        lines.append(f"rag_label_accuracy: {result.rag_label_accuracy:.4f}")
    else:
        lines.append("rag_label_accuracy: (no labels)")
    if result.react_label_accuracy is not None:
        lines.append(f"react_label_accuracy: {result.react_label_accuracy:.4f}")
    else:
        lines.append("react_label_accuracy: (no labels)")
    if result.avg_react_tool_calls is not None:
        lines.append(f"avg_react_tool_calls: {result.avg_react_tool_calls:.4f}")
    else:
        lines.append("avg_react_tool_calls: (none)")
    if result.avg_react_llm_calls is not None:
        lines.append(f"avg_react_llm_calls: {result.avg_react_llm_calls:.4f}")
    else:
        lines.append("avg_react_llm_calls: (none)")
    return "\n".join(lines)


def format_answer_probe_result(result: EvalAnswerProbeResult) -> str:
    lines = [
        f"dataset: {result.name}",
        f"retriever: {result.retriever}",
        f"query_id: {result.query_id or '(ad hoc)'}",
        f"elapsed_ms: {result.elapsed_ms}",
        f"timed_out: {str(result.timed_out).lower()}",
        f"retrieval_limit: {result.retrieval_limit}",
        f"evidence_limit: {result.evidence_limit}",
        f"first_relevant_rank: {result.first_relevant_rank}",
        f"evidence_contains_relevant: {str(result.evidence_contains_relevant).lower()}",
        f"answer_cites_relevant: {str(result.answer_cites_relevant).lower()}",
        f"expected_labels: {', '.join(result.expected_labels) or '(none)'}",
        f"predicted_label: {result.predicted_label or '(none)'}",
        f"label_correct: {_format_optional_bool(result.label_correct)}",
        f"relevant_doc_ids: {', '.join(result.relevant_doc_ids) or '(none)'}",
        f"evidence_doc_ids: {', '.join(result.evidence_doc_ids) or '(none)'}",
        f"cited_doc_ids: {', '.join(result.cited_doc_ids) or '(none)'}",
    ]
    if result.error:
        lines.append(f"error: {result.error}")
    lines.extend(["", "answer:", result.answer or "(no answer)"])
    return "\n".join(lines)


def _answer_run_result(
    *,
    name: str,
    retriever: str,
    queries_total: int,
    queries_skipped: int,
    timeout_seconds: float,
    concurrency: int,
    total_elapsed_ms: int,
    per_query: list[dict[str, Any]],
) -> EvalAnswerRunResult:
    evaluated = len(per_query)
    denom = max(1, evaluated)
    timed_out = sum(1 for row in per_query if row.get("timed_out"))
    evidence_hits = sum(1 for row in per_query if row.get("evidence_contains_relevant"))
    citation_hits = sum(1 for row in per_query if row.get("answer_cites_relevant"))
    label_rows = [row for row in per_query if row.get("label_correct") is not None]
    label_hits = sum(1 for row in label_rows if row.get("label_correct"))
    ranks = [
        int(row["first_relevant_rank"])
        for row in per_query
        if row.get("first_relevant_rank") is not None
    ]
    usage: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    for row in per_query:
        row_usage = row.get("usage") or {}
        if not isinstance(row_usage, Mapping):
            continue
        for key in usage:
            usage[key] += int(row_usage.get(key) or 0)
    return EvalAnswerRunResult(
        name=name,
        retriever=retriever,
        queries_total=queries_total,
        queries_evaluated=evaluated,
        queries_skipped=queries_skipped,
        timed_out=timed_out,
        timeout_seconds=timeout_seconds,
        concurrency=concurrency,
        total_elapsed_ms=total_elapsed_ms,
        answer_citation_hit_rate=citation_hits / denom,
        evidence_hit_rate=evidence_hits / denom,
        no_relevant_evidence_rate=(evaluated - evidence_hits) / denom,
        avg_first_relevant_rank=(sum(ranks) / len(ranks)) if ranks else None,
        labels_evaluated=len(label_rows),
        label_accuracy=(label_hits / len(label_rows)) if label_rows else None,
        usage=usage,
        per_query=per_query,
    )


def _report_compare_result(
    *,
    name: str,
    queries_total: int,
    queries_skipped: int,
    timeout_seconds: float,
    concurrency: int,
    total_elapsed_ms: int,
    per_query: list[dict[str, Any]],
) -> EvalReportCompareResult:
    evaluated = len(per_query)
    denom = max(1, evaluated)
    timed_out = sum(1 for row in per_query if row.get("timed_out"))
    rag_wins = 0
    react_wins = 0
    ties = 0
    judge_errors = 0
    rag_citation_hits = 0
    react_citation_hits = 0
    rag_label_rows: list[dict[str, Any]] = []
    react_label_rows: list[dict[str, Any]] = []
    react_tool_calls: list[int] = []
    react_llm_calls: list[int] = []
    usage = _empty_compare_usage()

    for row in per_query:
        judge = row.get("judge") if isinstance(row.get("judge"), Mapping) else {}
        winner = str((judge or {}).get("winner") or "tie").lower()
        if winner == "rag":
            rag_wins += 1
        elif winner == "react":
            react_wins += 1
        else:
            ties += 1
        if (judge or {}).get("error"):
            judge_errors += 1

        rag = row.get("rag") if isinstance(row.get("rag"), Mapping) else {}
        react = row.get("react") if isinstance(row.get("react"), Mapping) else {}
        if (rag or {}).get("answer_cites_relevant"):
            rag_citation_hits += 1
        if (react or {}).get("answer_cites_relevant"):
            react_citation_hits += 1
        if (rag or {}).get("label_correct") is not None:
            rag_label_rows.append(dict(rag or {}))
        if (react or {}).get("label_correct") is not None:
            react_label_rows.append(dict(react or {}))
        if (react or {}).get("tool_calls") is not None:
            react_tool_calls.append(int((react or {}).get("tool_calls") or 0))
        if (react or {}).get("llm_calls") is not None:
            react_llm_calls.append(int((react or {}).get("llm_calls") or 0))

        _accumulate_compare_usage(usage, "rag", (rag or {}).get("usage"))
        _accumulate_compare_usage(usage, "react", (react or {}).get("usage"))
        _accumulate_compare_usage(usage, "judge", (judge or {}).get("usage"))

    rag_label_hits = sum(1 for row in rag_label_rows if row.get("label_correct"))
    react_label_hits = sum(1 for row in react_label_rows if row.get("label_correct"))
    return EvalReportCompareResult(
        name=name,
        queries_total=queries_total,
        queries_evaluated=evaluated,
        queries_skipped=queries_skipped,
        timed_out=timed_out,
        timeout_seconds=timeout_seconds,
        concurrency=concurrency,
        total_elapsed_ms=total_elapsed_ms,
        rag_wins=rag_wins,
        react_wins=react_wins,
        ties=ties,
        judge_errors=judge_errors,
        rag_citation_hit_rate=rag_citation_hits / denom,
        react_citation_hit_rate=react_citation_hits / denom,
        rag_label_accuracy=(
            rag_label_hits / len(rag_label_rows) if rag_label_rows else None
        ),
        react_label_accuracy=(
            react_label_hits / len(react_label_rows) if react_label_rows else None
        ),
        avg_react_tool_calls=(
            sum(react_tool_calls) / len(react_tool_calls) if react_tool_calls else None
        ),
        avg_react_llm_calls=(
            sum(react_llm_calls) / len(react_llm_calls) if react_llm_calls else None
        ),
        usage=usage,
        per_query=per_query,
    )


def _empty_compare_usage() -> dict[str, int]:
    usage: dict[str, int] = {}
    for prefix in ("rag", "react", "judge", "total"):
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
        ):
            usage[f"{prefix}_{key}"] = 0
    return usage


def _accumulate_compare_usage(
    usage: dict[str, int],
    prefix: str,
    row_usage: Any,
) -> None:
    if not isinstance(row_usage, Mapping):
        return
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    ):
        value = int(row_usage.get(key) or 0)
        usage[f"{prefix}_{key}"] += value
        usage[f"total_{key}"] += value


def _format_optional_bool(value: bool | None) -> str:
    if value is None:
        return "(none)"
    return str(value).lower()
