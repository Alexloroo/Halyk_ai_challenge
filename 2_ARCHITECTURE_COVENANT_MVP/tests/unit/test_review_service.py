from datetime import date
from decimal import Decimal

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantResult,
    CovenantSpec,
    MetricSpec,
    SourceRef,
)
from halyk_covenants.review import (
    ReviewCase,
    ReviewDecision,
    SimilarityMatch,
    SimilarReviewCase,
)
from halyk_covenants.review.service import ReviewService


class FakeReviewer:
    model_name = "fake-reviewer"
    prompt_version = "test-v1"

    def __init__(self, decisions: list[ReviewDecision | Exception]) -> None:
        self.decisions = list(decisions)
        self.calls: list[list[SimilarityMatch]] = []

    def review(
        self,
        case: ReviewCase,
        *,
        similar_cases: list[SimilarityMatch] | None = None,
    ) -> ReviewDecision:
        del case
        self.calls.append(list(similar_cases or []))
        value = self.decisions.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeRetriever:
    def __init__(self, matches: list[SimilarityMatch] | Exception) -> None:
        self.matches = matches
        self.calls: list[tuple[str, int, float]] = []

    def search(
        self,
        query_text: str,
        *,
        k: int,
        minimum_similarity: float,
    ) -> list[SimilarityMatch]:
        self.calls.append((query_text, k, minimum_similarity))
        if isinstance(self.matches, Exception):
            raise self.matches
        return self.matches


def covenant(confidence: float = 0.95) -> CovenantSpec:
    return CovenantSpec(
        covenant_id="COV-1",
        raw_text="Monthly outgoing KZT must not exceed 15M KZT",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="sum", field="amount", unit="KZT"),
        condition=ConditionSpec(comparator="<=", threshold=Decimal("15000000"), currency="KZT"),
        source=SourceRef(document_id="DOC-1", page=1),
        confidence=confidence,
    )


def answer(*, status: str = "success") -> CovenantResult:
    return CovenantResult(
        borrower_id="B001",
        covenant_id="COV-1",
        verdict="violated",
        number=Decimal("16000000"),
        number_unit="KZT",
        evidence_transaction_id="TX-CURRENT",
        status=status,
    )


def review_case(
    *,
    verification_issues: list[str] | None = None,
    compiler_confidence: Decimal | None = Decimal("0.95"),
    status: str = "success",
) -> ReviewCase:
    return ReviewCase(
        case_id="case-current",
        borrower_id="B001",
        covenant_id="COV-1",
        evaluation_date=date(2026, 4, 30),
        question="Was the monthly outgoing limit violated?",
        answer=answer(status=status),
        rationale="16M > 15M",
        covenant=covenant(),
        verification_issues=verification_issues or [],
        compiler_confidence=compiler_confidence,
    )


def decision(
    confidence: str,
    *,
    accepted: bool = True,
    number: str = "16000000",
    verdict: str = "violated",
    evidence: str | None = "TX-CURRENT",
) -> ReviewDecision:
    return ReviewDecision(
        accepted=accepted,
        confidence=Decimal(confidence),
        verdict=verdict,
        number=Decimal(number),
        evidence_transaction_id=evidence,
        rationale="reviewed",
    )


def similar_match() -> SimilarityMatch:
    return SimilarityMatch(
        case=SimilarReviewCase(
            case_id="gold-1",
            question="Similar monthly outgoing question",
            covenant_type="financial",
            metric_type="sum",
            answer=CovenantResult(
                borrower_id="B999",
                covenant_id="COV-X",
                verdict="violated",
                number=Decimal("99999999"),
                evidence_transaction_id="TX-FOREIGN",
                status="success",
            ),
            rationale="validated historical case",
            embedding_text="monthly outgoing sum threshold",
        ),
        similarity=0.93,
    )


def test_high_confidence_skips_similarity() -> None:
    reviewer = FakeReviewer([decision("0.90")])
    retriever = FakeRetriever([similar_match()])
    service = ReviewService(reviewer=reviewer, similarity_retriever=retriever)

    reviewed = service.review(review_case())

    assert reviewed.review_status == "accepted"
    assert len(reviewer.calls) == 1
    assert retriever.calls == []


def test_low_confidence_calls_similarity_and_reviews_again() -> None:
    reviewer = FakeReviewer([decision("0.60"), decision("0.88")])
    retriever = FakeRetriever([similar_match()])
    service = ReviewService(reviewer=reviewer, similarity_retriever=retriever)

    reviewed = service.review(review_case())

    assert reviewed.review_status == "accepted_after_similarity"
    assert reviewed.review.used_similarity_fallback is True
    assert reviewed.review.similar_case_ids == ["gold-1"]
    assert len(reviewer.calls) == 2
    assert len(retriever.calls) == 1


def test_verifier_issue_forces_fallback_even_with_high_confidence() -> None:
    reviewer = FakeReviewer([decision("0.95"), decision("0.91")])
    retriever = FakeRetriever([similar_match()])
    service = ReviewService(reviewer=reviewer, similarity_retriever=retriever)

    reviewed = service.review(review_case(verification_issues=["pair mismatch"]))

    assert reviewed.review_status == "accepted_after_similarity"
    assert len(retriever.calls) == 1


def test_non_success_result_forces_fallback() -> None:
    reviewer = FakeReviewer([decision("0.95"), decision("0.91")])
    retriever = FakeRetriever([similar_match()])
    service = ReviewService(reviewer=reviewer, similarity_retriever=retriever)

    service.review(review_case(status="partial"))

    assert len(retriever.calls) == 1


def test_low_compiler_confidence_forces_fallback() -> None:
    reviewer = FakeReviewer([decision("0.95"), decision("0.91")])
    retriever = FakeRetriever([similar_match()])
    service = ReviewService(
        reviewer=reviewer,
        similarity_retriever=retriever,
        compiler_confidence_threshold=Decimal("0.70"),
    )

    service.review(review_case(compiler_confidence=Decimal("0.60")))

    assert len(retriever.calls) == 1


def test_reviewer_cannot_replace_deterministic_number() -> None:
    reviewer = FakeReviewer([decision("0.90", number="99999999")])
    service = ReviewService(reviewer=reviewer, similarity_retriever=FakeRetriever([]))

    reviewed = service.review(review_case())

    assert reviewed.review_status == "invalid_reviewer_output"
    assert reviewed.result.number == Decimal("16000000")


def test_reviewer_cannot_inject_foreign_evidence() -> None:
    reviewer = FakeReviewer([decision("0.90", evidence="TX-FOREIGN")])
    service = ReviewService(reviewer=reviewer, similarity_retriever=FakeRetriever([]))

    reviewed = service.review(review_case())

    assert reviewed.review_status == "invalid_reviewer_output"
    assert reviewed.result.evidence_transaction_id == "TX-CURRENT"


def test_reviewer_verdict_cannot_contradict_comparator() -> None:
    reviewer = FakeReviewer([decision("0.90", verdict="complied")])
    service = ReviewService(reviewer=reviewer, similarity_retriever=FakeRetriever([]))

    reviewed = service.review(review_case())

    assert reviewed.review_status == "invalid_reviewer_output"
    assert reviewed.result.verdict == "violated"


def test_second_review_still_uncertain_keeps_deterministic_result() -> None:
    reviewer = FakeReviewer([decision("0.60"), decision("0.65", accepted=False)])
    service = ReviewService(
        reviewer=reviewer,
        similarity_retriever=FakeRetriever([similar_match()]),
    )

    reviewed = service.review(review_case())

    assert reviewed.review_status == "low_confidence"
    assert reviewed.result.number == Decimal("16000000")


def test_empty_corpus_does_not_crash() -> None:
    reviewer = FakeReviewer([decision("0.60"), decision("0.62", accepted=False)])
    service = ReviewService(reviewer=reviewer, similarity_retriever=FakeRetriever([]))

    reviewed = service.review(review_case())

    assert reviewed.review_status == "low_confidence"
    assert reviewer.calls[1] == []


def test_embedding_failure_keeps_deterministic_result_and_marks_review_failed() -> None:
    reviewer = FakeReviewer([decision("0.60")])
    service = ReviewService(
        reviewer=reviewer,
        similarity_retriever=FakeRetriever(RuntimeError("embedding unavailable")),
    )

    reviewed = service.review(review_case())

    assert reviewed.review_status == "review_failed"
    assert reviewed.result.number == Decimal("16000000")


def test_reviewer_failure_keeps_deterministic_result_and_marks_review_failed() -> None:
    reviewer = FakeReviewer([RuntimeError("reviewer unavailable")])
    service = ReviewService(reviewer=reviewer, similarity_retriever=FakeRetriever([]))

    reviewed = service.review(review_case())

    assert reviewed.review_status == "review_failed"
    assert reviewed.result.number == Decimal("16000000")
