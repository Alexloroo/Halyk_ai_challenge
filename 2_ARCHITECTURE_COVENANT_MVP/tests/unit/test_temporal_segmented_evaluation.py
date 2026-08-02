from datetime import date
from decimal import Decimal

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantSpec,
    EvidenceMode,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
)
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.evaluators.temporal import TemporalEvaluationService
from halyk_covenants.storage import DuckDBStore


def _version(
    covenant_id: str,
    threshold: str,
    effective_from: date,
    effective_to: date | None,
) -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        covenant_group_id="COV-A2",
        raw_text="Maximum individual outgoing transaction",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="max", field="amount", unit="KZT"),
        condition=ConditionSpec(
            comparator="<=",
            threshold=Decimal(threshold),
            unit="KZT",
            currency="KZT",
        ),
        time_window=TimeWindowSpec(type="calendar_month"),
        evidence_mode=EvidenceMode.VIOLATING_TRANSACTION,
        effective_from=effective_from,
        effective_to=effective_to,
        source=SourceRef(document_id=f"{covenant_id}.pdf", page=1),
        confidence=1,
    )


def test_mid_month_amendment_checks_transaction_against_version_active_on_transaction_date() -> None:
    store = DuckDBStore()
    store.connection.execute(
        """
        INSERT INTO transactions VALUES
        ('TX-A2', 'B001', NULL, DATE '2026-04-10', 6000000, 'KZT', 'outgoing',
         NULL, NULL, NULL, 'row-1', 'fixture.csv', 'hash'),
        ('TX-A3', 'B001', NULL, DATE '2026-04-20', 5000000, 'KZT', 'outgoing',
         NULL, NULL, NULL, 'row-2', 'fixture.csv', 'hash')
        """
    )
    versions = [
        _version("COV-A2-v1", "5500000", date(2026, 1, 1), date(2026, 4, 14)),
        _version("COV-A2-v2", "6500000", date(2026, 4, 15), None),
    ]

    result = TemporalEvaluationService(EvaluationService(store)).evaluate_versions(
        versions,
        borrower_id="B001",
        evaluation_date=date(2026, 4, 30),
    )

    assert result.covenant_id == "COV-A2"
    assert result.number == Decimal("6000000.000000")
    assert result.verdict == "violated"
    assert result.evidence_transaction_id == "TX-A2"
    assert result.status == "success"
    store.close()


def test_version_spanning_aggregate_rule_is_explicitly_rejected_when_semantics_are_ambiguous() -> None:
    store = DuckDBStore()
    versions = [
        _version("COV-A2-v1", "5500000", date(2026, 1, 1), date(2026, 4, 14)),
        _version("COV-A2-v2", "6500000", date(2026, 4, 15), None),
    ]
    versions = [
        version.model_copy(
            update={
                "metric": MetricSpec(metric_type="sum", field="amount", unit="KZT"),
                "evidence_mode": EvidenceMode.NONE,
            }
        )
        for version in versions
    ]

    result = TemporalEvaluationService(EvaluationService(store)).evaluate_versions(
        versions,
        borrower_id="B001",
        evaluation_date=date(2026, 4, 30),
    )

    assert result.verdict == "unknown"
    assert result.status == "failed"
    assert result.failure_stage.value == "temporal"
    assert "version changes inside one metric window" in result.errors[0]
    store.close()
