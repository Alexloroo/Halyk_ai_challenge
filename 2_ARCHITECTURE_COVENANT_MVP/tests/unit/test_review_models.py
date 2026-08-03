from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantResult,
    CovenantSpec,
    MetricSpec,
    SourceRef,
)
from halyk_covenants.review import ReviewCase, ReviewDecision, ReviewedResult


def covenant() -> CovenantSpec:
    return CovenantSpec(
        covenant_id="COV-1",
        raw_text="Monthly outgoing amount must not exceed 15M KZT",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="sum", field="amount", unit="KZT"),
        condition=ConditionSpec(comparator="<=", threshold=Decimal("15000000"), currency="KZT"),
        source=SourceRef(document_id="DOC-1", page=1),
        confidence=0.92,
    )


def result() -> CovenantResult:
    return CovenantResult(
        borrower_id="B001",
        covenant_id="COV-1",
        verdict="violated",
        number=Decimal("16000000"),
        number_unit="KZT",
        calculation_id="calc-1",
        status="success",
    )


def test_review_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision(
            accepted=True,
            confidence=Decimal("1.01"),
            verdict="violated",
            number=Decimal("16000000"),
            rationale="supported",
        )


def test_review_case_requires_strict_fields() -> None:
    case = ReviewCase(
        case_id="case-1",
        borrower_id="B001",
        covenant_id="COV-1",
        evaluation_date=date(2026, 4, 30),
        question="Was COV-1 violated?",
        answer=result(),
        rationale="16M > 15M",
        covenant=covenant(),
        verification_issues=[],
        compiler_confidence=Decimal("0.92"),
    )
    assert case.answer.number == Decimal("16000000")
    assert case.compiler_confidence == Decimal("0.92")


def test_reviewed_result_status_is_constrained() -> None:
    decision = ReviewDecision(
        accepted=True,
        confidence=Decimal("0.90"),
        verdict="violated",
        number=Decimal("16000000"),
        rationale="supported",
    )
    reviewed = ReviewedResult(result=result(), review=decision, review_status="accepted")
    assert reviewed.review_status == "accepted"

    with pytest.raises(ValidationError):
        ReviewedResult(result=result(), review=decision, review_status="made_up")
