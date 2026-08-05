"""Cloud1 verification: manifest completeness oracle, confidence model, dual path."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from halyk_covenants.domain import (
    Calculation,
    ConditionSpec,
    CovenantResult,
    CovenantSpec,
    MetricSpec,
    SourceRef,
)
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.verification import (
    DualPathVerifier,
    ManifestBuilder,
    ResultVerifier,
    build_confidence_report,
    compute_confidence,
)


def build_spec(covenant_id: str, borrower_ids: list[str], **overrides) -> CovenantSpec:
    payload = {
        "covenant_id": covenant_id,
        "raw_text": "Outgoing payments must not exceed 50,000,000 KZT per month.",
        "borrower_ids": borrower_ids,
        "metric": MetricSpec(metric_type="sum", field="amount"),
        "condition": ConditionSpec(comparator="<=", threshold=Decimal("50000000")),
        "source": SourceRef(document_id="doc-1", page=1),
        "confidence": 0.9,
    }
    payload.update(overrides)
    return CovenantSpec(**payload)


def build_result(borrower_id: str, covenant_id: str, **overrides) -> CovenantResult:
    payload = {
        "borrower_id": borrower_id,
        "covenant_id": covenant_id,
        "verdict": "complied",
        "number": Decimal("16000000"),
        "status": "success",
    }
    payload.update(overrides)
    return CovenantResult(**payload)


# --- expectation manifest: the completeness oracle --------------------------------


def test_manifest_from_organizer_questions_is_independent_of_detection():
    """The whole point: a question the detector never found still lands in the manifest."""
    with DuckDBStore(":memory:") as store:
        from halyk_covenants.covenants import CovenantRegistry

        registry = CovenantRegistry(store)
        manifest = ManifestBuilder(store, registry).build(
            questions={("B001", "COV-NEVER-DETECTED"): "Did B001 comply?"}
        )

    assert ("B001", "COV-NEVER-DETECTED") in manifest.expected_pairs
    assert ("B001", "COV-NEVER-DETECTED") in manifest.required_pairs


def test_manifest_expands_group_covenants_across_all_borrowers():
    with DuckDBStore(":memory:") as store:
        from halyk_covenants.covenants import CovenantRegistry

        registry = CovenantRegistry(store)
        registry.save(build_spec("COV-G", ["B001", "B002", "B003"], scope_mode="group"))
        manifest = ManifestBuilder(store, registry).build()

    assert manifest.expected_pairs == {
        ("B001", "COV-G"),
        ("B002", "COV-G"),
        ("B003", "COV-G"),
    }


def test_completeness_check_detects_a_missing_result_against_the_manifest():
    """This is the check that could never fire in codex-1/codex-2."""
    with DuckDBStore(":memory:") as store:
        from halyk_covenants.covenants import CovenantRegistry

        registry = CovenantRegistry(store)
        manifest = ManifestBuilder(store, registry).build(
            questions={
                ("B001", "COV-1"): "q1",
                ("B002", "COV-2"): "q2",
            }
        )

    results = [build_result("B001", "COV-1")]  # B002/COV-2 is missing
    report = ResultVerifier().verify(list(manifest.expected_pairs), results)

    codes = {issue.code for issue in report.issues}
    assert "missing_result" in codes
    assert report.valid is False


def test_manifest_survives_a_round_trip_through_storage():
    with DuckDBStore(":memory:") as store:
        from halyk_covenants.covenants import CovenantRegistry

        registry = CovenantRegistry(store)
        builder = ManifestBuilder(store, registry)
        builder.build(questions={("B001", "COV-1"): "q"})
        reloaded = builder.load()

    assert ("B001", "COV-1") in reloaded.expected_pairs


# --- confidence model: deterministic, not an LLM number ---------------------------


@pytest.mark.parametrize(
    ("spec_trust", "flags", "status", "expected"),
    [
        ("accepted", {"dual_path_mismatch"}, "success", "unreliable"),
        ("accepted", set(), "failed", "unreliable"),
        ("low", set(), "success", "low"),
        ("accepted", {"evidence_mismatch"}, "success", "low"),
        ("revised", set(), "success", "medium"),
        ("accepted", set(), "partial", "medium"),
        ("accepted", {"empty_input_rows"}, "success", "medium"),
        ("accepted", set(), "success", "high"),
    ],
)
def test_confidence_rules_fire_in_priority_order(spec_trust, flags, status, expected):
    spec = build_spec("COV-1", ["B001"], spec_trust=spec_trust, review_confidence=0.9)
    result = build_result("B001", "COV-1", status=status)
    assert compute_confidence(result, spec, flags) == expected


def test_low_llm_confidence_downgrades_high_to_medium():
    """LLM confidence is one input among several, never the sole gate."""
    spec = build_spec("COV-1", ["B001"], review_confidence=0.4, confidence=0.9)
    result = build_result("B001", "COV-1")
    assert compute_confidence(result, spec, set()) == "medium"


def test_triage_ranking_puts_the_worst_answers_first():
    specs = {
        "COV-A": build_spec("COV-A", ["B001"], spec_trust="accepted", review_confidence=0.9),
        "COV-B": build_spec("COV-B", ["B001"], spec_trust="low", review_confidence=0.2),
        "COV-C": build_spec("COV-C", ["B001"], spec_trust="revised", review_confidence=0.8),
    }
    results = [
        build_result("B001", "COV-A"),
        build_result("B001", "COV-B"),
        build_result("B001", "COV-C"),
    ]
    flags = {("B001", "COV-A"): {"dual_path_mismatch"}}

    report = build_confidence_report(results, specs, flags)

    assert [entry.covenant_id for entry in report] == ["COV-A", "COV-B", "COV-C"]
    assert [entry.level for entry in report] == ["unreliable", "low", "medium"]
    assert [entry.triage_rank for entry in report] == [1, 2, 3]


def test_confidence_report_carries_the_reviewer_objection_to_the_human():
    specs = {
        "COV-1": build_spec(
            "COV-1", ["B001"], spec_trust="low", review_objection="metric should be count"
        )
    }
    report = build_confidence_report([build_result("B001", "COV-1")], specs, {})

    assert report[0].review_objection == "metric should be count"


# --- dual path: recompute the number through a different code path ----------------


def _seed_transactions(store: DuckDBStore) -> None:
    store.connection.execute(
        """
        INSERT INTO transactions VALUES
          ('TX-1','B001','ACC-1',DATE '2026-04-01',5000000,'KZT','outgoing',
           NULL,NULL,NULL,NULL,'seed','h'),
          ('TX-2','B001','ACC-1',DATE '2026-04-10',6000000,'KZT','outgoing',
           NULL,NULL,NULL,NULL,'seed','h')
        """
    )


def test_dual_path_accepts_a_matching_recomputation():
    with DuckDBStore(":memory:") as store:
        _seed_transactions(store)
        calculation = Calculation(
            calculation_id="calc-1",
            covenant_id="COV-1",
            borrower_ids=["B001"],
            metric_type="sum",
            sql="SELECT SUM(amount) FROM transactions WHERE borrower_id = ?",
            parameter_summary=["B001"],
            input_row_count=2,
            value=Decimal("11000000"),
        )
        issues = DualPathVerifier(store).verify(build_result("B001", "COV-1"), calculation)

    assert issues == []


def test_dual_path_flags_a_mismatched_value_as_non_repairable():
    with DuckDBStore(":memory:") as store:
        _seed_transactions(store)
        calculation = Calculation(
            calculation_id="calc-1",
            covenant_id="COV-1",
            borrower_ids=["B001"],
            metric_type="sum",
            sql="SELECT SUM(amount) FROM transactions WHERE borrower_id = ?",
            parameter_summary=["B001"],
            input_row_count=2,
            value=Decimal("99999999"),  # deliberately wrong
        )
        issues = DualPathVerifier(store).verify(build_result("B001", "COV-1"), calculation)

    assert [issue.code for issue in issues] == ["dual_path_mismatch"]
    assert issues[0].classification == "non_repairable"


def test_dual_path_is_a_no_op_without_recorded_sql():
    with DuckDBStore(":memory:") as store:
        assert DualPathVerifier(store).verify(build_result("B001", "COV-1"), None) == []


def test_dual_path_reports_broken_sql_instead_of_crashing_the_run():
    with DuckDBStore(":memory:") as store:
        calculation = Calculation(
            calculation_id="calc-1",
            covenant_id="COV-1",
            borrower_ids=["B001"],
            metric_type="sum",
            sql="SELECT SUM(amount) FROM table_that_does_not_exist",
            parameter_summary=[],
            input_row_count=0,
            value=Decimal("1"),
        )
        issues = DualPathVerifier(store).verify(build_result("B001", "COV-1"), calculation)

    assert [issue.code for issue in issues] == ["dual_path_error"]


# --- calculation_id collision fix -------------------------------------------------


def test_group_covenant_gives_each_borrower_a_distinct_calculation_id():
    """Regression: group scope used to hash to one id, so all but one borrower was lost."""
    from halyk_covenants.evaluators import EvaluationService

    spec = build_spec("COV-G", ["B001", "B002"], scope_mode="group")

    with DuckDBStore(":memory:") as store:
        _seed_transactions(store)
        store.connection.execute(
            """
            INSERT INTO transactions VALUES
              ('TX-3','B002','ACC-2',DATE '2026-04-05',7000000,'KZT','outgoing',
               NULL,NULL,NULL,NULL,'seed','h')
            """
        )
        service = EvaluationService(store)
        first = service.evaluate(spec, "B001", date(2026, 4, 30))
        second = service.evaluate(spec, "B002", date(2026, 4, 30))

        stored = store.connection.execute("SELECT COUNT(*) FROM calculations").fetchone()[0]

    assert first.calculation_id != second.calculation_id
    assert stored == 2, "both borrowers must keep their own provenance row"
