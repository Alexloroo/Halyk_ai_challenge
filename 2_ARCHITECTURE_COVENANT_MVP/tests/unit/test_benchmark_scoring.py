from decimal import Decimal

from halyk_covenants.benchmark import summarize_scores
from halyk_covenants.benchmark.scoring import score_answer
from halyk_covenants.domain import CovenantResult
from halyk_covenants.synthetic.models import ExpectedAnswer


def expected(
    number: Decimal | int | None,
    verdict: str = "violated",
    evidence: str | None = None,
) -> ExpectedAnswer:
    return ExpectedAnswer(
        number=number,
        verdict=verdict,  # type: ignore[arg-type]
        evidence_transaction_id=evidence,
        status="success",
        explanation="Hand-derived fixture expectation.",
    )


def actual(
    number: Decimal | int | None,
    verdict: str = "violated",
    evidence: str | None = None,
) -> CovenantResult:
    return CovenantResult(
        borrower_id="B001",
        covenant_id="C1",
        number=number,
        verdict=verdict,  # type: ignore[arg-type]
        evidence_transaction_id=evidence,
        status="success",
    )


def test_score_compares_numeric_values_without_float_conversion() -> None:
    score = score_answer(
        "CASE-1",
        expected(Decimal("0.100000000000000000000001")),
        actual(Decimal("0.100000000000000000000001")),
    )

    assert score.number_score == 1
    assert score.component_score == 3
    assert score.full_exact_match is True


def test_score_accepts_equivalent_integer_and_decimal_count() -> None:
    score = score_answer("CASE-1", expected(3), actual(Decimal("3")))

    assert score.number_score == 1


def test_score_handles_null_number_and_null_evidence_as_exact_components() -> None:
    score = score_answer(
        "CASE-1",
        expected(None, verdict="unknown"),
        actual(None, verdict="unknown"),
    )

    assert score.number_score == 1
    assert score.evidence_score == 1
    assert score.full_exact_match is True


def test_evidence_miss_preserves_number_and_verdict_credit() -> None:
    score = score_answer(
        "CASE-1",
        expected(3, evidence="A003"),
        actual(3, evidence=None),
    )

    assert score.number_score == 1
    assert score.verdict_score == 1
    assert score.evidence_score == 0
    assert score.component_score == 2
    assert score.full_exact_match is False


def test_summary_computes_component_and_exact_match_rates_from_literal_scores() -> None:
    exact = score_answer("CASE-1", expected(1), actual(1))
    evidence_miss = score_answer("CASE-2", expected(2, evidence="TX2"), actual(2, evidence=None))

    summary = summarize_scores([exact, evidence_miss])

    assert summary.total_cases == 2
    assert summary.earned_components == 5
    assert summary.maximum_components == 6
    assert summary.number_accuracy == Decimal("1")
    assert summary.verdict_accuracy == Decimal("1")
    assert summary.evidence_accuracy == Decimal("0.5")
    assert summary.component_accuracy == Decimal("0.8333333333333333333333333333")
    assert summary.full_exact_match_accuracy == Decimal("0.5")
    assert summary.failed_case_ids == ["CASE-2"]
