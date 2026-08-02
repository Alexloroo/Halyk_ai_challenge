from __future__ import annotations

from datetime import date
from decimal import Decimal

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantSpec,
    EvidenceMode,
    FilterSpec,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
)
from halyk_covenants.evals.scoring import score_compiler_output
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.evidence import EvidenceContext, EvidenceValidator
from halyk_covenants.sql import build_where_clause
from halyk_covenants.storage import DuckDBStore


def _insert(store: DuckDBStore, tx: str, amount: int, direction: str) -> None:
    store.connection.execute(
        """
        INSERT INTO transactions VALUES
        (?, 'B001', NULL, DATE '2026-04-10', ?, 'KZT', ?,
         NULL, NULL, NULL, ?, 'fixture.csv', ?)
        """,
        [tx, amount, direction, tx, f"hash-{tx}"],
    )


def test_ratio_metric_supports_independent_numerator_filters() -> None:
    store = DuckDBStore()
    _insert(store, "IN", 30, "incoming")
    _insert(store, "OUT", 70, "outgoing")
    spec = CovenantSpec(
        covenant_id="COV-RATIO",
        raw_text="Incoming payments must be no more than 50 percent of all payments",
        borrower_ids=["B001"],
        metric=MetricSpec(
            metric_type="ratio",
            numerator=MetricSpec(
                metric_type="sum",
                field="amount",
                filters=[FilterSpec(field="direction", operator="eq", value="incoming")],
            ),
            denominator=MetricSpec(metric_type="sum", field="amount"),
            unit="ratio",
        ),
        condition=ConditionSpec(comparator="<=", threshold=Decimal("0.5"), unit="ratio"),
        time_window=TimeWindowSpec(type="calendar_month"),
        source=SourceRef(document_id="contract.pdf", page=1),
        confidence=1,
    )

    result = EvaluationService(store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == Decimal("0.3")
    assert result.verdict == "complied"
    store.close()


def test_successful_evaluation_persists_calculation_ledger() -> None:
    store = DuckDBStore()
    _insert(store, "A", 30, "incoming")
    _insert(store, "B", 70, "outgoing")
    spec = CovenantSpec(
        covenant_id="COV-SUM",
        raw_text="Total must not exceed 200 KZT",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="sum", field="amount", unit="KZT"),
        condition=ConditionSpec(comparator="<=", threshold=200, unit="KZT", currency="KZT"),
        transaction_filters=[FilterSpec(field="currency", operator="eq", value="KZT")],
        source=SourceRef(document_id="contract.pdf", page=1),
        confidence=1,
    )

    result = EvaluationService(store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.calculation_id is not None
    payload = store.connection.execute(
        "SELECT calculation_json FROM calculations WHERE calculation_id = ?",
        [result.calculation_id],
    ).fetchone()
    assert payload is not None
    assert '"input_row_count":2' in payload[0].replace(" ", "")
    assert '"value":"100.000000"' in payload[0].replace(" ", "")
    store.close()


def test_evidence_validator_rejects_in_scope_transaction_that_is_not_selected_maximum() -> None:
    store = DuckDBStore()
    _insert(store, "LOW", 4_000_000, "outgoing")
    _insert(store, "HIGH", 9_000_000, "outgoing")
    spec = CovenantSpec(
        covenant_id="COV-MAX",
        raw_text="Maximum must not exceed 5000000 KZT",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="max", field="amount", unit="KZT"),
        condition=ConditionSpec(comparator="<=", threshold=5_000_000, currency="KZT"),
        evidence_mode=EvidenceMode.MAX_TRANSACTION,
        source=SourceRef(document_id="contract.pdf", page=1),
        confidence=1,
    )
    where_sql, parameters = build_where_clause("B001", [], evaluation_date=date(2026, 4, 30))

    verification = EvidenceValidator().validate(
        "LOW",
        EvidenceContext(
            covenant=spec,
            borrower_id="B001",
            db=store,
            where_sql=where_sql,
            parameters=parameters,
        ),
    )

    assert verification.valid is False
    assert any("maximum" in error for error in verification.errors)
    store.close()


def test_compiler_scoring_covers_exclusions_effective_dates_grouping_and_nested_metrics() -> None:
    expected = CovenantSpec(
        covenant_id="COV-X",
        raw_text="ratio rule",
        borrower_ids=["B001"],
        metric=MetricSpec(
            metric_type="ratio",
            numerator=MetricSpec(
                metric_type="sum",
                field="amount",
                filters=[FilterSpec(field="direction", operator="eq", value="incoming")],
            ),
            denominator=MetricSpec(metric_type="sum", field="amount"),
            unit="ratio",
        ),
        condition=ConditionSpec(comparator="<=", threshold=Decimal("0.5"), unit="ratio"),
        exclusions=[FilterSpec(field="purpose", operator="contains", value="internal")],
        group_by=["counterparty_id"],
        effective_from=date(2026, 1, 1),
        source=SourceRef(document_id="contract.pdf", page=1),
        confidence=1,
    )
    actual = expected.model_copy(
        update={
            "exclusions": [],
            "group_by": [],
            "effective_from": date(2026, 2, 1),
            "metric": MetricSpec(
                metric_type="ratio",
                numerator=MetricSpec(metric_type="sum", field="amount"),
                denominator=MetricSpec(metric_type="sum", field="amount"),
                unit="ratio",
            ),
        }
    )

    scores = score_compiler_output(expected, actual)

    assert scores["exclusions_exact"] == 0
    assert scores["group_by_exact"] == 0
    assert scores["effective_dates_exact"] == 0
    assert scores["nested_metric_exact"] == 0
