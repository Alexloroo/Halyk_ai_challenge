from datetime import date
from decimal import Decimal

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantSpec,
    EvidenceMode,
    MetricSpec,
    SourceRef,
)
from halyk_covenants.evaluators import EvaluationService, EvaluatorRegistry, MaxEvaluator
from halyk_covenants.storage import DuckDBStore


def make_spec(metric_type: str = "max") -> CovenantSpec:
    if metric_type == "ratio":
        metric = MetricSpec(
            metric_type="ratio",
            numerator=MetricSpec(metric_type="sum", field="amount"),
            denominator=MetricSpec(metric_type="sum", field="amount"),
        )
    else:
        metric = MetricSpec(metric_type=metric_type, field="amount")  # type: ignore[arg-type]
    return CovenantSpec(
        covenant_id="COV-1",
        raw_text="Synthetic rule",
        borrower_ids=["B001"],
        metric=metric,
        condition=ConditionSpec(comparator="<=", threshold=5),
        evidence_mode=EvidenceMode.VIOLATING_TRANSACTION,
        source=SourceRef(document_id="fixture", page=1),
        confidence=1,
    )


class EvidenceFailingMaxEvaluator(MaxEvaluator):
    def select_evidence(self, *args: object, **kwargs: object) -> str | None:
        raise RuntimeError("evidence lookup unavailable")


def test_evidence_failure_preserves_correct_number_and_verdict_as_partial() -> None:
    store = DuckDBStore()
    store.connection.execute(
        """
        INSERT INTO transactions VALUES
        ('TX9', 'B001', NULL, DATE '2026-01-01', 9, 'KZT', 'outgoing',
         NULL, NULL, NULL, 'row-1', 'fixture.csv', 'hash')
        """
    )
    registry = EvaluatorRegistry({"max": EvidenceFailingMaxEvaluator()})

    result = EvaluationService(store, registry).evaluate(make_spec(), "B001", date(2026, 1, 1))

    assert result.number == Decimal("9.000000")
    assert result.verdict == "violated"
    assert result.evidence_transaction_id is None
    assert result.status == "partial"
    assert result.errors == ["evidence selection failed: evidence lookup unavailable"]
    store.close()


def test_metric_missing_from_custom_registry_becomes_failed_result_instead_of_raising() -> None:
    store = DuckDBStore()

    result = EvaluationService(store, EvaluatorRegistry({})).evaluate(
        make_spec("ratio"), "B001", date(2026, 1, 1)
    )

    assert result.number is None
    assert result.verdict == "unknown"
    assert result.status == "failed"
    assert result.errors == ["unsupported metric type: ratio"]
    store.close()
