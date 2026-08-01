from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from halyk_covenants.domain import (
    Borrower,
    ConditionSpec,
    CovenantResult,
    CovenantSpec,
    EvidenceMode,
    FilterSpec,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
    Transaction,
)


def test_transaction_preserves_leading_zero_id_and_exact_decimal_amount() -> None:
    transaction = Transaction(
        transaction_id="0001",
        borrower_id="000341",
        transaction_date=date(2026, 4, 1),
        amount="10.010000",
    )

    assert transaction.transaction_id == "0001"
    assert transaction.borrower_id == "000341"
    assert transaction.amount == Decimal("10.010000")


def test_transaction_rejects_numeric_identifier_instead_of_losing_leading_zeroes() -> None:
    with pytest.raises(ValidationError):
        Transaction(
            transaction_id=1,  # type: ignore[arg-type]
            transaction_date=date(2026, 4, 1),
            amount="1.00",
        )


def test_collection_defaults_are_not_shared_between_models() -> None:
    first = Borrower(borrower_id="B001")
    second = Borrower(borrower_id="B002")

    first.aliases.append("Alpha")

    assert second.aliases == []


@pytest.mark.parametrize(
    "window",
    [
        {"type": "rolling_days"},
        {"type": "rolling_days", "rolling_days": 0},
        {"type": "custom", "start_date": "2026-04-30", "end_date": "2026-04-01"},
    ],
)
def test_time_window_rejects_incomplete_or_invalid_ranges(window: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TimeWindowSpec.model_validate(window)


def test_ratio_metric_requires_numerator_and_denominator() -> None:
    with pytest.raises(ValidationError):
        MetricSpec(metric_type="ratio")


def test_covenant_spec_keeps_compiler_contract_and_provenance() -> None:
    covenant = CovenantSpec(
        covenant_id="COV-1",
        raw_text="Monthly outgoing payments must not exceed 10M KZT.",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="sum", field="amount", unit="money"),
        condition=ConditionSpec(comparator="<=", threshold="10000000", currency="KZT"),
        transaction_filters=[FilterSpec(field="direction", operator="eq", value="outgoing")],
        time_window=TimeWindowSpec(type="calendar_month"),
        evidence_mode=EvidenceMode.NONE,
        source=SourceRef(document_id="DOC-1", page=2),
        confidence=0.95,
    )

    assert covenant.condition.threshold == Decimal("10000000")
    assert covenant.source.page == 2
    assert covenant.borrower_ids == ["B001"]


def test_covenant_requires_at_least_one_borrower() -> None:
    with pytest.raises(ValidationError):
        CovenantSpec(
            covenant_id="COV-1",
            raw_text="Rule",
            borrower_ids=[],
            metric=MetricSpec(metric_type="count", field="transaction_id"),
            condition=ConditionSpec(comparator="<=", threshold=5),
            source=SourceRef(document_id="DOC-1", page=1),
            confidence=1,
        )


def test_result_error_lists_are_independent() -> None:
    first = CovenantResult(
        borrower_id="B001",
        covenant_id="C1",
        verdict="unknown",
        status="failed",
    )
    second = CovenantResult(
        borrower_id="B002",
        covenant_id="C2",
        verdict="unknown",
        status="failed",
    )

    first.errors.append("failed")

    assert second.errors == []
