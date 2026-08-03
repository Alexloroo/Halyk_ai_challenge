from __future__ import annotations

from decimal import Decimal

from halyk_covenants.evaluators.comparator import compare
from halyk_covenants.observability import annotate_current_trace, trace_context, trace_stage
from halyk_covenants.review.models import ReviewCase, ReviewDecision, ReviewedResult, SimilarityMatch
from halyk_covenants.review.reviewer import Reviewer
from halyk_covenants.review.similarity import SimilarityRetriever


class InvalidReviewerDecision(ValueError):
    pass


class ReviewService:
    def __init__(
        self,
        *,
        reviewer: Reviewer,
        similarity_retriever: SimilarityRetriever,
        confidence_threshold: Decimal = Decimal("0.70"),
        compiler_confidence_threshold: Decimal = Decimal("0.70"),
        similarity_top_k: int = 5,
        minimum_similarity: float = 0.55,
    ) -> None:
        if not Decimal("0") <= confidence_threshold <= Decimal("1"):
            raise ValueError("confidence_threshold must be between 0 and 1")
        if not Decimal("0") <= compiler_confidence_threshold <= Decimal("1"):
            raise ValueError("compiler_confidence_threshold must be between 0 and 1")
        if similarity_top_k <= 0:
            raise ValueError("similarity_top_k must be positive")
        if not -1 <= minimum_similarity <= 1:
            raise ValueError("minimum_similarity must be between -1 and 1")
        self.reviewer = reviewer
        self.similarity_retriever = similarity_retriever
        self.confidence_threshold = confidence_threshold
        self.compiler_confidence_threshold = compiler_confidence_threshold
        self.similarity_top_k = similarity_top_k
        self.minimum_similarity = minimum_similarity

    @trace_stage("review.case", run_type="chain", tags=("review",))
    def review(self, case: ReviewCase) -> ReviewedResult:
        metadata = {
            "borrower_id": case.borrower_id,
            "covenant_id": case.covenant_id,
            "result_status": case.answer.status,
            "reviewer_model": getattr(self.reviewer, "model_name", type(self.reviewer).__name__),
            "reviewer_prompt_version": getattr(self.reviewer, "prompt_version", None),
        }
        annotate_current_trace(metadata={key: value for key, value in metadata.items() if value})
        with trace_context(**metadata):
            try:
                first = self._first_pass(case)
                self._validate_decision(case, first)
            except InvalidReviewerDecision as exc:
                return self._invalid(case, str(exc))
            except Exception as exc:
                return self._failed(case, f"first review failed: {exc}")

            reasons = self._fallback_reasons(case, first)
            annotate_current_trace(
                metadata={
                    "first_review_confidence": str(first.confidence),
                    "fallback_triggered": bool(reasons),
                    "fallback_reasons": reasons,
                }
            )
            if not reasons:
                return ReviewedResult(
                    result=case.answer,
                    review=first,
                    review_status="accepted",
                )

            try:
                matches = self.similarity_retriever.search(
                    self._embedding_text(case),
                    k=self.similarity_top_k,
                    minimum_similarity=self.minimum_similarity,
                )
            except Exception as exc:
                decision = first.model_copy(
                    update={
                        "accepted": False,
                        "issues": [*first.issues, f"similarity retrieval failed: {exc}"],
                    }
                )
                return ReviewedResult(
                    result=case.answer,
                    review=decision,
                    review_status="review_failed",
                    fallback_reasons=reasons,
                )

            similarity_scores = {
                match.case.case_id: match.similarity for match in matches
            }
            annotate_current_trace(
                metadata={
                    "similar_case_ids": [match.case.case_id for match in matches],
                    "similarity_scores": similarity_scores,
                }
            )
            try:
                second = self._second_pass(case, matches)
                second = second.model_copy(
                    update={
                        "used_similarity_fallback": True,
                        "similar_case_ids": [match.case.case_id for match in matches],
                    }
                )
                self._validate_decision(case, second)
            except InvalidReviewerDecision as exc:
                return self._invalid(
                    case,
                    str(exc),
                    used_similarity=True,
                    matches=matches,
                    fallback_reasons=reasons,
                )
            except Exception as exc:
                return self._failed(
                    case,
                    f"second review failed: {exc}",
                    used_similarity=True,
                    matches=matches,
                    fallback_reasons=reasons,
                )

            if second.accepted and second.confidence >= self.confidence_threshold:
                return ReviewedResult(
                    result=case.answer,
                    review=second,
                    review_status="accepted_after_similarity",
                    fallback_reasons=reasons,
                    similarity_scores=similarity_scores,
                )
            return ReviewedResult(
                result=case.answer,
                review=second,
                review_status="low_confidence",
                fallback_reasons=reasons,
                similarity_scores=similarity_scores,
            )

    @trace_stage("review.first_pass", run_type="llm", tags=("review",))
    def _first_pass(self, case: ReviewCase) -> ReviewDecision:
        return self.reviewer.review(case, similar_cases=[])

    @trace_stage("review.second_pass", run_type="llm", tags=("review", "similarity"))
    def _second_pass(
        self,
        case: ReviewCase,
        matches: list[SimilarityMatch],
    ) -> ReviewDecision:
        return self.reviewer.review(case, similar_cases=matches)

    @trace_stage("review.validate", run_type="tool", tags=("review", "deterministic"))
    def _validate_decision(self, case: ReviewCase, decision: ReviewDecision) -> None:
        if not self._same_number(decision.number, case.answer.number):
            raise InvalidReviewerDecision("reviewer changed deterministic number")
        if decision.evidence_transaction_id not in {
            None,
            case.answer.evidence_transaction_id,
        }:
            raise InvalidReviewerDecision("reviewer injected foreign evidence transaction")
        threshold = case.covenant.condition.threshold
        if case.answer.number is not None and threshold is not None:
            expected_verdict = (
                "complied"
                if compare(
                    case.answer.number,
                    case.covenant.condition.comparator,
                    threshold,
                )
                else "violated"
            )
            if decision.verdict != expected_verdict:
                raise InvalidReviewerDecision(
                    "reviewer verdict contradicts deterministic comparator"
                )
        elif decision.verdict != case.answer.verdict:
            raise InvalidReviewerDecision("reviewer changed verdict without numeric proof")

    def _fallback_reasons(self, case: ReviewCase, first: ReviewDecision) -> list[str]:
        reasons: list[str] = []
        if first.confidence < self.confidence_threshold:
            reasons.append("review_confidence")
        if not first.accepted:
            reasons.append("review_rejected")
        if case.verification_issues:
            reasons.append("verification_issue")
        if case.answer.status != "success":
            reasons.append("result_status")
        if (
            case.compiler_confidence is not None
            and case.compiler_confidence < self.compiler_confidence_threshold
        ):
            reasons.append("compiler_confidence")
        return reasons

    @staticmethod
    def _embedding_text(case: ReviewCase) -> str:
        return (
            f"QUESTION:\n{case.question}\n\n"
            f"METRIC:\n{case.covenant.metric.metric_type}\n\n"
            f"RULE:\n{case.covenant.raw_text}"
        )

    @staticmethod
    def _same_number(left: Decimal | int | None, right: Decimal | int | None) -> bool:
        if left is None or right is None:
            return left is right
        return Decimal(str(left)) == Decimal(str(right))

    def _invalid(
        self,
        case: ReviewCase,
        issue: str,
        *,
        used_similarity: bool = False,
        matches: list[SimilarityMatch] | None = None,
        fallback_reasons: list[str] | None = None,
    ) -> ReviewedResult:
        decision = self._fallback_decision(
            case,
            issue,
            used_similarity=used_similarity,
            matches=matches,
        )
        return ReviewedResult(
            result=case.answer,
            review=decision,
            review_status="invalid_reviewer_output",
            fallback_reasons=list(fallback_reasons or []),
            similarity_scores=self._scores(matches),
        )

    def _failed(
        self,
        case: ReviewCase,
        issue: str,
        *,
        used_similarity: bool = False,
        matches: list[SimilarityMatch] | None = None,
        fallback_reasons: list[str] | None = None,
    ) -> ReviewedResult:
        decision = self._fallback_decision(
            case,
            issue,
            used_similarity=used_similarity,
            matches=matches,
        )
        return ReviewedResult(
            result=case.answer,
            review=decision,
            review_status="review_failed",
            fallback_reasons=list(fallback_reasons or []),
            similarity_scores=self._scores(matches),
        )

    @staticmethod
    def _scores(matches: list[SimilarityMatch] | None) -> dict[str, float]:
        return {match.case.case_id: match.similarity for match in matches or []}

    @staticmethod
    def _fallback_decision(
        case: ReviewCase,
        issue: str,
        *,
        used_similarity: bool,
        matches: list[SimilarityMatch] | None,
    ) -> ReviewDecision:
        return ReviewDecision(
            accepted=False,
            confidence=Decimal("0"),
            verdict=case.answer.verdict,
            number=case.answer.number,
            evidence_transaction_id=case.answer.evidence_transaction_id,
            rationale=case.rationale,
            issues=[issue],
            used_similarity_fallback=used_similarity,
            similar_case_ids=[match.case.case_id for match in matches or []],
        )
