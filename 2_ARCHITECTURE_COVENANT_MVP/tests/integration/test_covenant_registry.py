from datetime import date
from pathlib import Path

from halyk_covenants.covenants import CovenantRegistry
from halyk_covenants.domain import ConditionSpec, CovenantSpec, MetricSpec, SourceRef
from halyk_covenants.storage import DuckDBStore


def spec(covenant_id: str, borrower_ids: list[str]) -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        covenant_group_id="LIMIT",
        raw_text="Monthly limit",
        borrower_ids=borrower_ids,
        metric=MetricSpec(metric_type="sum", field="amount"),
        condition=ConditionSpec(comparator="<=", threshold=10, currency="KZT"),
        effective_from=date(2026, 1, 1),
        source=SourceRef(document_id="d1", page=2),
        confidence=0.9,
    )


def test_registry_round_trips_strict_spec_and_borrower_lookup(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "registry.duckdb") as store:
        registry = CovenantRegistry(store)
        registry.save(spec("C1", ["B001", "B002"]))
        registry.save(spec("C2", ["B002"]))

        restored = registry.list()
        for_borrower = registry.for_borrower("B001")

        assert [item.covenant_id for item in restored] == ["C1", "C2"]
        assert [item.covenant_id for item in for_borrower] == ["C1"]
        assert restored[0].condition.threshold == 10
        assert restored[0].source.page == 2


def test_registry_save_is_idempotent(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "idempotent.duckdb") as store:
        registry = CovenantRegistry(store)
        registry.save(spec("C1", ["B001"]))
        registry.save(spec("C1", ["B001"]))

        assert len(registry.list()) == 1
        count = store.connection.execute("SELECT COUNT(*) FROM covenant_borrowers").fetchone()[0]
        assert count == 1
