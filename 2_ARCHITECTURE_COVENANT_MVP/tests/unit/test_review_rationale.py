from datetime import UTC, date, datetime
from decimal import Decimal

from halyk_covenants.domain import (
    Calculation,
    ConditionSpec,
    CovenantResult,
    CovenantSpec,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
)
from halyk_covenants.review import ReviewCase, build_rationale


def test_rationale_contains_calculation_and_comparison() -> None:
    covenant = CovenantSpec(
        covenant_id="COV-1",
        raw_text="Monthly outgoing KZT must not exceed 15,000,000 KZT.",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="sum", field="amount", unit="KZT"),
        condition=ConditionSpec(comparator="<=", threshold=Decimal("15000000"), currency="KZT"),
        time_window=TimeWindowSpec(type="calendar_month"),
        source=SourceRef(document_id="DOC-1", page=1),
        confidence=0.95,
    )
    answer = CovenantResult(
        borrower_id="B001",
        covenant_id="COV-1",
        verdict="violated",
        number=Decimal("16000000"),
        number_unit="KZT",
        calculation_id="calc-1",
        status="success",
    )
    calculation = Calculation(
        calculation_id="calc-1",
        covenant_id="COV-1",
        borrower_ids=["B001"],
        metric_type="sum",
        sql="SELECT SUM(amount) FROM transactions WHERE borrower_id = ?",
        parameter_summary=["B001"],
        input_row_count=3,
        value=Decimal("16000000"),
        unit="KZT",
        created_at=datetime(2026, 4, 30, tzinfo=UTC),
    )
    case = ReviewCase(
        case_id="case-1",
        borrower_id="B001",
        covenant_id="COV-1",
        evaluation_date=date(2026, 4, 30),
        question="Was COV-1 violated?",
        answer=answer,
        rationale="",
        covenant=covenant,
        calculation=calculation,
        verification_issues=[],
    )

    rationale = build_rationale(case)

    assert "16000000" in rationale
    assert "15000000" in rationale
    assert "violated" in rationale
    assert "3 matched transactions" in rationale
    assert "calendar_month" in rationale
