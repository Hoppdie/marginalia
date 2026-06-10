"""Dataclasses shared by the evaluation package."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class EvalImportResult:
    name: str
    dataset_dir: Path
    docs_imported: int
    queries: int
    qrels: int
    split: str
    resumed: bool = False
    concurrency: int = 1


@dataclass(slots=True)
class EvalRunResult:
    name: str
    retriever: str
    queries_total: int
    queries_evaluated: int
    queries_skipped: int
    zero_result_rate: float
    no_relevant_at_k_rate: float
    mrr: float
    hit_rate: dict[int, float]
    recall: dict[int, float]
    ndcg: dict[int, float]
    per_query: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class EvalAblationConfig:
    name: str
    retriever: str
    plan_phase: bool = False
    semantic_recall: bool = False
    rerank: bool = False
    relation_expansion: bool = False


@dataclass(slots=True)
class EvalAblationRunResult:
    name: str
    k_values: list[int]
    query_limit: int | None
    runs: list[dict[str, Any]]


@dataclass(slots=True)
class EvalAnswerProbeResult:
    name: str
    retriever: str
    query_id: str | None
    query: str
    timed_out: bool
    timeout_seconds: float
    elapsed_ms: int
    retrieval_limit: int
    evidence_limit: int
    relevant_doc_ids: list[str]
    ranked_doc_ids: list[str]
    evidence_doc_ids: list[str]
    cited_entry_ids: list[str]
    cited_doc_ids: list[str]
    expected_labels: list[str]
    predicted_label: str | None
    label_correct: bool | None
    first_relevant_rank: int | None
    evidence_contains_relevant: bool
    answer_cites_relevant: bool
    answer: str
    usage: dict[str, int]
    error: str | None = None


@dataclass(slots=True)
class EvalAnswerRunResult:
    name: str
    retriever: str
    queries_total: int
    queries_evaluated: int
    queries_skipped: int
    timed_out: int
    timeout_seconds: float
    concurrency: int
    total_elapsed_ms: int
    answer_citation_hit_rate: float
    evidence_hit_rate: float
    no_relevant_evidence_rate: float
    avg_first_relevant_rank: float | None
    labels_evaluated: int
    label_accuracy: float | None
    usage: dict[str, int]
    per_query: list[dict[str, Any]]


@dataclass(slots=True)
class EvalReportCompareResult:
    name: str
    queries_total: int
    queries_evaluated: int
    queries_skipped: int
    timed_out: int
    timeout_seconds: float
    concurrency: int
    total_elapsed_ms: int
    rag_wins: int
    react_wins: int
    ties: int
    judge_errors: int
    rag_citation_hit_rate: float
    react_citation_hit_rate: float
    rag_label_accuracy: float | None
    react_label_accuracy: float | None
    avg_react_tool_calls: float | None
    avg_react_llm_calls: float | None
    usage: dict[str, int]
    per_query: list[dict[str, Any]]


@dataclass(slots=True)
class BeirDocument:
    doc_id: str
    title: str
    text: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class BeirQuery:
    query_id: str
    text: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class _ExistingEvalEntry:
    file_id: str
    entry_id: str
    ingested: bool
