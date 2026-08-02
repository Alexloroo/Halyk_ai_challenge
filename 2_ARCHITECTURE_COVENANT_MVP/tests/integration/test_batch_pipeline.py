from datetime import date
from pathlib import Path

from halyk_covenants.covenants import CovenantRegistry
from halyk_covenants.domain import (
    ConditionSpec,
    CovenantSpec,
    FilterSpec,
    MetricSpec,
    SourceRef,
)
from halyk_covenants.pipeline import BatchEvaluationPipeline
from halyk_covenants.storage import DuckDBStore


def spec(covenant_id: str, filters: list[FilterSpec]) -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        raw_text="sum <= 100",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="sum", field="amount"),
        condition=ConditionSpec(comparator="<=", threshold=100),
        transaction_filters=filters,
        source=SourceRef(document_id="d1", page=1),
        confidence=1,
    )


def test_batch_keeps_failed_pair_and_preserves_completeness(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "batch.duckdb") as store:
        store.connection.execute(
            """
            INSERT INTO transactions VALUES
            ('K1', 'B001', NULL, DATE '2026-04-01', 50, 'KZT', NULL,
             NULL, NULL, NULL, '1', 'f', 'h1'),
            ('U1', 'B001', NULL, DATE '2026-04-01', 5, 'USD', NULL,
             NULL, NULL, NULL, '2', 'f', 'h2')
            """
        )
        registry = CovenantRegistry(store)
        registry.save(spec("GOOD", [FilterSpec(field="currency", operator="eq", value="KZT")]))
        registry.save(spec("FAILED", []))

        report = BatchEvaluationPipeline(store, registry).run(date(2026, 4, 30))

        assert report.expected_pair_count == report.actual_pair_count == 2
        assert {result.status for result in report.results} == {"success", "failed"}
        assert store.connection.execute("SELECT COUNT(*) FROM covenant_results").fetchone()[0] == 2
