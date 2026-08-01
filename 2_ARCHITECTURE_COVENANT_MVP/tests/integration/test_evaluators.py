from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantSpec,
    EvidenceMode,
    FilterSpec,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
)
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.storage import DuckDBStore


@pytest.fixture
def transaction_store(tmp_path: Path) -> DuckDBStore:
    source = tmp_path / "transactions.csv"
    source.write_text(
        "transaction_id,borrower_id,date,amount,currency,direction\n"
        "TX1,B001,2026-04-01,4000000.000000,KZT,outgoing\n"
        "TX2,B001,2026-04-10,6000000.000000,KZT,outgoing\n"
        "TX3,B001,2026-04-20,2000000.000000,KZT,outgoing\n"
        "TX4,B001,2026-04-21,100.000000,KZT,incoming\n"
        "TX5,B001,2026-05-01,9000000.000000,KZT,outgoing\n"
        "TX6,B002,2026-04-05,99000000.000000,KZT,outgoing\n",
        encoding="utf-8",
    )
    store = DuckDBStore(tmp_path / "evaluation.duckdb")
    store.load_transactions(source)
    yield store
    store.close()


def covenant(
    metric_type: str,
    field: str,
    comparator: str,
    threshold: str | int,
    *,
    filters: list[FilterSpec] | None = None,
    evidence_mode: EvidenceMode = EvidenceMode.NONE,
) -> CovenantSpec:
    return CovenantSpec(
        covenant_id=f"COV-{metric_type}",
        raw_text="Synthetic rule",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type=metric_type, field=field, unit="KZT"),  # type: ignore[arg-type]
        condition=ConditionSpec(comparator=comparator, threshold=threshold),  # type: ignore[arg-type]
        transaction_filters=filters or [],
        time_window=TimeWindowSpec(type="calendar_month"),
        evidence_mode=evidence_mode,
        source=SourceRef(document_id="fixture", page=1),
        confidence=1,
    )


def test_sum_uses_filters_borrower_scope_and_calendar_month(
    transaction_store: DuckDBStore,
) -> None:
    spec = covenant(
        "sum",
        "amount",
        "<=",
        "11000000",
        filters=[FilterSpec(field="direction", operator="eq", value="outgoing")],
    )

    result = EvaluationService(transaction_store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == Decimal("12000000.000000")
    assert result.verdict == "violated"
    assert result.status == "success"
    assert result.evidence_transaction_id is None


def test_sum_exact_boundary_is_complied(transaction_store: DuckDBStore) -> None:
    spec = covenant(
        "sum",
        "amount",
        "<=",
        "12000000",
        filters=[FilterSpec(field="direction", operator="eq", value="outgoing")],
    )

    result = EvaluationService(transaction_store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == Decimal("12000000.000000")
    assert result.verdict == "complied"


def test_count_applies_amount_filter_and_returns_integer(transaction_store: DuckDBStore) -> None:
    spec = covenant(
        "count",
        "transaction_id",
        "<=",
        1,
        filters=[
            FilterSpec(field="direction", operator="eq", value="outgoing"),
            FilterSpec(field="amount", operator="gt", value=Decimal("3000000")),
        ],
    )

    result = EvaluationService(transaction_store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == 2
    assert isinstance(result.number, int)
    assert result.verdict == "violated"


@pytest.mark.parametrize(
    ("metric_type", "expected"),
    [
        ("max", Decimal("6000000.000000")),
        ("min", Decimal("2000000.000000")),
        ("avg", Decimal("4000000.000000")),
    ],
)
def test_decimal_aggregate_metrics_are_computed_without_float(
    transaction_store: DuckDBStore,
    metric_type: str,
    expected: Decimal,
) -> None:
    spec = covenant(
        metric_type,
        "amount",
        "<=",
        "99999999",
        filters=[FilterSpec(field="direction", operator="eq", value="outgoing")],
    )

    result = EvaluationService(transaction_store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == expected
    assert isinstance(result.number, Decimal)
    assert result.verdict == "complied"


def test_max_violation_returns_deterministic_evidence_transaction(
    transaction_store: DuckDBStore,
) -> None:
    spec = covenant(
        "max",
        "amount",
        "<=",
        "5000000",
        filters=[FilterSpec(field="direction", operator="eq", value="outgoing")],
        evidence_mode=EvidenceMode.VIOLATING_TRANSACTION,
    )

    result = EvaluationService(transaction_store).evaluate(spec, "B001", date(2026, 4, 30))

    assert result.number == Decimal("6000000.000000")
    assert result.verdict == "violated"
    assert result.evidence_transaction_id == "TX2"
    assert result.status == "success"


@pytest.mark.parametrize("metric_type", ["max", "min", "avg"])
def test_undefined_empty_aggregates_return_partial_unknown(
    transaction_store: DuckDBStore, metric_type: str
) -> None:
    spec = covenant(metric_type, "amount", "<=", "100")

    result = EvaluationService(transaction_store).evaluate(spec, "B001", date(2026, 6, 30))

    assert result.number is None
    assert result.verdict == "unknown"
    assert result.status == "partial"
    assert result.errors == ["metric is undefined for an empty transaction set"]


def test_sum_and_count_empty_sets_return_zero(transaction_store: DuckDBStore) -> None:
    service = EvaluationService(transaction_store)

    sum_result = service.evaluate(covenant("sum", "amount", "<=", "0"), "B001", date(2026, 6, 30))
    count_result = service.evaluate(
        covenant("count", "transaction_id", "==", 0), "B001", date(2026, 6, 30)
    )

    assert sum_result.number == Decimal("0.000000")
    assert sum_result.verdict == "complied"
    assert count_result.number == 0
    assert count_result.verdict == "complied"
