from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantSpec,
    FilterSpec,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
)
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.storage import DuckDBStore


@pytest.fixture
def store(tmp_path: Path) -> DuckDBStore:
    source = tmp_path / "advanced.csv"
    source.write_text(
        "transaction_id,borrower_id,date,amount,currency,direction,counterparty_id,purpose\n"
        "A001,B001,2026-04-01,40,KZT,outgoing,CP-A,goods\n"
        "A002,B001,2026-04-01,20,KZT,outgoing,CP-B,goods\n"
        "A003,B001,2026-04-01,15,KZT,outgoing,CP-B,goods\n"
        "A004,B001,2026-04-01,25,KZT,outgoing,CP-C,tax payment\n"
        "A005,B001,2026-04-02,5,USD,outgoing,BANNED,goods\n"
        "B001,B002,2026-04-01,30,KZT,outgoing,CP-D,goods\n",
        encoding="utf-8",
    )
    database = DuckDBStore(tmp_path / "advanced.duckdb")
    database.load_transactions(source)
    yield database
    database.close()


def covenant(
    covenant_id: str,
    metric: MetricSpec,
    condition: ConditionSpec,
    *,
    filters: list[FilterSpec] | None = None,
    exclusions: list[FilterSpec] | None = None,
    group_by: list[str] | None = None,
    borrower_ids: list[str] | None = None,
    scope_mode: str = "per_borrower",
) -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        raw_text="Synthetic advanced rule",
        borrower_ids=borrower_ids or ["B001"],
        scope_mode=scope_mode,  # type: ignore[arg-type]
        metric=metric,
        condition=condition,
        transaction_filters=filters or [],
        exclusions=exclusions or [],
        group_by=group_by or [],
        time_window=TimeWindowSpec(type="calendar_month"),
        source=SourceRef(document_id="fixture", page=1),
        confidence=1,
    )


def kzt_filter() -> list[FilterSpec]:
    return [FilterSpec(field="currency", operator="eq", value="KZT")]


def test_ratio_returns_max_counterparty_share(store: DuckDBStore) -> None:
    spec = covenant(
        "RATIO",
        MetricSpec(
            metric_type="ratio",
            numerator=MetricSpec(metric_type="sum", field="amount"),
            denominator=MetricSpec(metric_type="sum", field="amount"),
            unit="ratio",
        ),
        ConditionSpec(comparator="<=", threshold=Decimal("0.30"), unit="ratio"),
        filters=kzt_filter(),
        group_by=["counterparty_id"],
    )

    result = EvaluationService(store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == Decimal("0.4")
    assert result.verdict == "violated"


def test_existence_counts_prohibited_transactions(store: DuckDBStore) -> None:
    spec = covenant(
        "EXISTS",
        MetricSpec(metric_type="existence", field="transaction_id", unit="count"),
        ConditionSpec(comparator="==", threshold=0, unit="count"),
        filters=[FilterSpec(field="counterparty_id", operator="eq", value="BANNED")],
    )

    result = EvaluationService(store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == 1
    assert result.verdict == "violated"


def test_frequency_returns_max_daily_count(store: DuckDBStore) -> None:
    spec = covenant(
        "FREQUENCY",
        MetricSpec(metric_type="frequency", field="transaction_id", unit="per_day"),
        ConditionSpec(comparator="<=", threshold=3, unit="per_day"),
        filters=kzt_filter(),
    )

    result = EvaluationService(store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == 4
    assert result.verdict == "violated"


def test_group_scope_combines_all_declared_borrowers(store: DuckDBStore) -> None:
    spec = covenant(
        "GROUP",
        MetricSpec(metric_type="sum", field="amount", unit="money"),
        ConditionSpec(comparator="==", threshold=130, currency="KZT"),
        filters=kzt_filter(),
        borrower_ids=["B001", "B002"],
        scope_mode="group",
    )

    result = EvaluationService(store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == Decimal("130.000000")
    assert result.verdict == "complied"


def test_exclusion_removes_tax_payments(store: DuckDBStore) -> None:
    spec = covenant(
        "EXCLUSION",
        MetricSpec(metric_type="sum", field="amount", unit="money"),
        ConditionSpec(comparator="==", threshold=75, currency="KZT"),
        filters=kzt_filter(),
        exclusions=[FilterSpec(field="purpose", operator="contains", value="tax")],
    )

    result = EvaluationService(store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == Decimal("75.000000")
    assert result.verdict == "complied"
