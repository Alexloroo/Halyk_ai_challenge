from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.covenants import CovenantRegistry
from halyk_covenants.domain import Calculation, CovenantResult, CovenantSpec
from halyk_covenants.observability import annotate_current_trace, trace_context, trace_stage
from halyk_covenants.pipeline.evaluate import BatchEvaluationReport
from halyk_covenants.review import ReviewCase, ReviewDecision, ReviewedResult, ReviewService
from halyk_covenants.review.rationale import build_rationale
from halyk_covenants.review.storage import ReviewDecisionStore
from halyk_covenants.storage import DuckDBStore


class ReviewedBatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_run_id: str
    evaluation_run_id: str
    evaluation_date: date
    reviewed_results: list[ReviewedResult]
    accepted_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    low_confidence_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)

    @property
    def authoritative_results(self) -> list[CovenantResult]:
        return [item.result for item in self.reviewed_results]


class ReviewPipeline:
    def __init__(
        self,
        store: DuckDBStore,
        *,
        service: ReviewService,
        registry: CovenantRegistry | None = None,
        decision_store: ReviewDecisionStore | None = None,
    ) -> None:
        self.store = store
        self.service = service
        self.registry = registry or CovenantRegistry(store)
        self.decision_store = decision_store or ReviewDecisionStore(store)

    @trace_stage("pipeline.review", run_type="chain", tags=("pipeline", "review"))
    def run(
        self,
        batch: BatchEvaluationReport,
        *,
        questions: Mapping[tuple[str, str], str] | None = None,
    ) -> ReviewedBatchReport:
        review_run_id = str(uuid4())
        annotate_current_trace(
            metadata={
                "review_run_id": review_run_id,
                "evaluation_run_id": batch.run_id,
                "evaluation_date": batch.evaluation_date.isoformat(),
                "review_case_count": len(batch.results),
            }
        )
        reviewed_results: list[ReviewedResult] = []
        for result in batch.results:
            with trace_context(
                review_run_id=review_run_id,
                borrower_id=result.borrower_id,
                covenant_id=result.covenant_id,
            ):
                calculation = self._load_calculation(result)
                spec, resolution_issues = self._resolve_spec(
                    result,
                    calculation,
                    batch.evaluation_date,
                )
                if spec is None:
                    reviewed = self._unreviewable(
                        result,
                        "review pipeline could not resolve covenant specification",
                    )
                else:
                    verification_issues = [
                        *self._verification_issues(batch, result),
                        *resolution_issues,
                    ]
                    question = (
                        questions.get((result.borrower_id, result.covenant_id))
                        if questions is not None
                        else None
                    ) or self._default_question(result, spec, batch.evaluation_date)
                    case = ReviewCase(
                        case_id=(
                            f"{batch.run_id}:{result.borrower_id}:{result.covenant_id}"
                        ),
                        borrower_id=result.borrower_id,
                        covenant_id=result.covenant_id,
                        evaluation_date=batch.evaluation_date,
                        question=question,
                        answer=result,
                        rationale="",
                        covenant=spec,
                        calculation=calculation,
                        verification_issues=verification_issues,
                        compiler_confidence=Decimal(str(spec.confidence)),
                    )
                    case = case.model_copy(update={"rationale": build_rationale(case)})
                    reviewed = self.service.review(case)
                self.decision_store.save(
                    review_run_id=review_run_id,
                    evaluation_date=batch.evaluation_date,
                    reviewed=reviewed,
                    reviewer_model=getattr(
                        self.service.reviewer,
                        "model_name",
                        type(self.service.reviewer).__name__,
                    ),
                    prompt_version=getattr(self.service.reviewer, "prompt_version", None),
                )
                reviewed_results.append(reviewed)

        return ReviewedBatchReport(
            review_run_id=review_run_id,
            evaluation_run_id=batch.run_id,
            evaluation_date=batch.evaluation_date,
            reviewed_results=reviewed_results,
            accepted_count=sum(
                item.review_status in {"accepted", "accepted_after_similarity"}
                for item in reviewed_results
            ),
            fallback_count=sum(item.review.used_similarity_fallback for item in reviewed_results),
            low_confidence_count=sum(
                item.review_status == "low_confidence" for item in reviewed_results
            ),
            failed_count=sum(
                item.review_status in {"invalid_reviewer_output", "review_failed"}
                for item in reviewed_results
            ),
        )

    def _load_calculation(self, result: CovenantResult) -> Calculation | None:
        if result.calculation_id is None:
            return None
        row = self.store.connection.execute(
            """
            SELECT calculation_json
            FROM calculations
            WHERE calculation_id = ? AND borrower_id = ?
            """,
            [result.calculation_id, result.borrower_id],
        ).fetchone()
        return Calculation.model_validate_json(row[0]) if row is not None else None

    def _resolve_spec(
        self,
        result: CovenantResult,
        calculation: Calculation | None,
        evaluation_date: date,
    ) -> tuple[CovenantSpec | None, list[str]]:
        candidates = [
            spec
            for spec in self.registry.for_borrower(result.borrower_id)
            if (spec.covenant_group_id or spec.covenant_id) == result.covenant_id
            or spec.covenant_id == result.covenant_id
        ]
        if not candidates:
            return None, []
        if calculation is not None:
            calculated_version = [
                spec for spec in candidates if spec.covenant_id == calculation.covenant_id
            ]
            if len(calculated_version) == 1:
                return calculated_version[0], []
        if len(candidates) == 1:
            return candidates[0], []
        active = [
            spec
            for spec in candidates
            if (spec.effective_from is None or spec.effective_from <= evaluation_date)
            and (spec.effective_to is None or evaluation_date <= spec.effective_to)
        ]
        if len(active) == 1:
            return active[0], [
                "review context resolved from active covenant version because calculation version "
                "was unavailable"
            ]
        chosen = sorted(
            candidates,
            key=lambda spec: (spec.effective_from or date.min, spec.covenant_id),
            reverse=True,
        )[0]
        return chosen, [
            "review context contains ambiguous covenant versions; selected latest candidate for "
            "review context only"
        ]

    @staticmethod
    def _verification_issues(
        batch: BatchEvaluationReport,
        result: CovenantResult,
    ) -> list[str]:
        return [
            issue.message
            for issue in batch.verification.issues
            if (issue.borrower_id is None or issue.borrower_id == result.borrower_id)
            and (issue.covenant_id is None or issue.covenant_id == result.covenant_id)
        ]

    @staticmethod
    def _default_question(
        result: CovenantResult,
        spec: CovenantSpec,
        evaluation_date: date,
    ) -> str:
        return (
            f"Does borrower {result.borrower_id} comply with the following covenant as of "
            f"{evaluation_date.isoformat()}? {spec.raw_text}"
        )

    @staticmethod
    def _unreviewable(result: CovenantResult, issue: str) -> ReviewedResult:
        decision = ReviewDecision(
            accepted=False,
            confidence=Decimal("0"),
            verdict=result.verdict,
            number=result.number,
            evidence_transaction_id=result.evidence_transaction_id,
            rationale=issue,
            issues=[issue],
        )
        return ReviewedResult(
            result=result,
            review=decision,
            review_status="review_failed",
        )
