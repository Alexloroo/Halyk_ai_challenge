from datetime import date
from decimal import Decimal

from halyk_covenants.domain import ConditionSpec, CovenantSpec, MetricSpec, SourceRef
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.storage import DuckDBStore


def test_mixed_currency_sum_without_fx_rule_fails_closed() -> None:
    store = DuckDBStore()
    store.connection.execute(
        """
        INSERT INTO transactions VALUES
        ('K1', 'B001', NULL, DATE '2026-04-01', 100, 'KZT', 'outgoing',
         NULL, NULL, NULL, '1', 'fixture', 'h1'),
        ('U1', 'B001', NULL, DATE '2026-04-02', 10, 'USD', 'outgoing',
         NULL, NULL, NULL, '2', 'fixture', 'h2')
        """
    )
    spec = CovenantSpec(
        covenant_id="MIXED",
        raw_text="Outgoing total",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="sum", field="amount", unit="money"),
        condition=ConditionSpec(comparator="<=", threshold=Decimal("1000")),
        source=SourceRef(document_id="fixture", page=1),
        confidence=1,
    )

    result = EvaluationService(store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.status == "failed"
    assert result.verdict == "unknown"
    assert result.number is None
    assert "mixed currencies" in result.errors[0]
    store.close()
