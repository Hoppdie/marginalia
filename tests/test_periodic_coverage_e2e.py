"""Contract test: every "auto-maintenance" kind must be wired into the
periodic dispatcher.

This catches the class of bug where a new task handler is registered but the
author forgets to add it to `PERIODIC_INTERVALS` — meaning it would never run
unless the user manually triggered something. test_dispatcher_e2e verifies the
loop dispatches *what is in* PERIODIC_INTERVALS; this test verifies the *right
things are in it*.

If you intentionally make a kind manual-only (e.g., `/tend` chains it), remove
it from EXPECTED_PERIODIC and document why.

Run:
    .venv/Scripts/python tests/test_periodic_coverage_e2e.py
"""
from __future__ import annotations

import sys

# Importing handlers populates the task-handler registry so registered_kinds()
# returns the full set. Without this, handlers that no other module has touched
# yet would be missing from the count.
import marginalia.tasks.handlers  # noqa: F401

from marginalia.tasks.kinds import (
    DEFAULT_PRIORITIES,
    KIND_ENRICH_TAGS,
    KIND_MINE_CITATION_GRAPH,
    KIND_MINE_CORPUS_EVIDENCE,
    KIND_MINE_SESSION_COOCCURRENCE,
    KIND_MINE_TAG_OVERLAP,
    KIND_NORMALIZE_TAGS,
    KIND_PERIODIC_TICK,
    KIND_PROPOSE_VIEWS,
    KIND_PRUNE_AUDIT_EVENTS,
    KIND_PRUNE_TASK_OUTCOMES,
    KIND_PURGE_DELETED_FILES,
    KIND_RECOVER_STUCK_TASKS,
    KIND_REFRESH_ENTRY_EXTRA,
    KIND_RESTRUCTURE_CATALOGS,
    KIND_SUGGEST_ARCHIVAL,
    KIND_SUGGEST_DEMOTION,
    KIND_VET_RELATIONS,
    PERIODIC_INTERVALS,
    registered_kinds,
)


# Kinds that the README and design.md describe as running on their own schedule.
# Source of truth lives here; if you change one, the dispatcher_e2e test will
# also pick up the change automatically.
EXPECTED_PERIODIC = {
    KIND_RECOVER_STUCK_TASKS,
    KIND_PURGE_DELETED_FILES,
    KIND_NORMALIZE_TAGS,
    KIND_ENRICH_TAGS,
    KIND_RESTRUCTURE_CATALOGS,
    KIND_SUGGEST_DEMOTION,
    KIND_SUGGEST_ARCHIVAL,
    KIND_MINE_SESSION_COOCCURRENCE,
    KIND_MINE_TAG_OVERLAP,
    KIND_MINE_CITATION_GRAPH,
    KIND_MINE_CORPUS_EVIDENCE,
    KIND_VET_RELATIONS,
    KIND_PROPOSE_VIEWS,
    KIND_REFRESH_ENTRY_EXTRA,
    KIND_PRUNE_AUDIT_EVENTS,
    KIND_PRUNE_TASK_OUTCOMES,
}


# Kinds that legitimately are NOT periodic. Listed explicitly so adding a new
# kind forces a conscious choice (does it go in EXPECTED_PERIODIC or here?).
EXPECTED_NON_PERIODIC = {
    "reflect_turn",
    "ingest_file",
    KIND_PERIODIC_TICK,
}


def main() -> None:
    actual = set(PERIODIC_INTERVALS.keys())
    missing = EXPECTED_PERIODIC - actual
    assert not missing, (
        f"PERIODIC_INTERVALS is missing kinds that should auto-run: {sorted(missing)}. "
        "Either add them to PERIODIC_INTERVALS in tasks/kinds.py or remove them "
        "from EXPECTED_PERIODIC in this test (with reason)."
    )
    print("[1] all", len(EXPECTED_PERIODIC), "expected periodic kinds are wired")

    extra = actual - EXPECTED_PERIODIC
    assert not extra, (
        f"PERIODIC_INTERVALS has kinds not listed as expected-periodic: {sorted(extra)}. "
        "Add them to EXPECTED_PERIODIC in this test."
    )
    print("[2] no surprise periodic kinds")

    # Every registered kind is accounted for: either periodic or explicitly not.
    classified = EXPECTED_PERIODIC | EXPECTED_NON_PERIODIC
    unclassified = set(registered_kinds()) - classified
    assert not unclassified, (
        f"these task kinds are registered but unclassified by this contract test: "
        f"{sorted(unclassified)}. Add them to EXPECTED_PERIODIC or "
        "EXPECTED_NON_PERIODIC."
    )
    print("[3] every registered kind is classified")

    # Every kind in PERIODIC_INTERVALS must have a priority — falling back to
    # 100 silently demotes mining work above online traffic.
    missing_priority = [
        k for k in PERIODIC_INTERVALS if k not in DEFAULT_PRIORITIES
    ]
    assert not missing_priority, (
        f"these periodic kinds have no DEFAULT_PRIORITY: {missing_priority}"
    )
    print("[4] all periodic kinds have an explicit priority")

    print("\nALL PERIODIC COVERAGE CHECKS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
