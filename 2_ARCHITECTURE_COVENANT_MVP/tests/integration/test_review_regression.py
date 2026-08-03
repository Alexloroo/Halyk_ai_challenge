from datetime import UTC, date, datetime
from decimal import Decimal

from halyk_covenants.covenants import CovenantRegistry
from halyk_covenants.domain import (
    Calculation,
    ConditionSpec,
    CovenantResult,
    CovenantSpec,
    MetricSpec,
    SourceRef,
)
from halyk_covenants.pipeline.evaluate import BatchEvaluationReport
from halyk_covenants.review import ReviewCase, ReviewDecision, SimilarReviewCase
from halyk_covenants.review_cli import run_review
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.verification import VerificationReport


class ConstantEmbedder:
    model_name = "constant"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


class CopyingReviewer:
    model_name = "fake"
    prompt_version = "test"

    def __init__(self) -> None:
        self.calls = 0

    def review(self, case: ReviewCase, *, similar_cases=None) -> ReviewDecision:
        self.calls += 1
        if self.calls == 1:
            return ReviewDecision(
                accepted=False,
                confidence=Decimal("0.60"),
                verdict=case.answer.verdict,
                number=case.answer.number,
                rationale="uncertain",
            )
        assert similar_cases
        foreign = similar_cases[0].case.answer
        return ReviewDecision(
            accepted=True,
            confidence=Decimal("0.95"),
            verdict=foreign.verdict,
            number=foreign.number,
            evidence_transaction_id=foreign.evidence_transaction_id,
            rationale="copied similar case",
        )


def test_high_cosine_similar_question_cannot_copy_foreign_answer() -> None:
    with DuckDBStore(":memory:") as store:
        spec = CovenantSpec(
            covenant_id="COV-CURRENT",
            raw_text="Monthly outgoing KZT must not exceed 10M KZT",
            borrower_ids=["B-CURRENT"],
            metric=MetricSpec(metric_type="sum", field="amount", unit="KZT"),
            condition=ConditionSpec(
                comparator="<=", threshold=Decimal("10000000"), currency="KZT"
            ),
            source=SourceRef(document_id="DOC-CURRENT", page=1),
            confidence=0.95,
        )
        CovenantRegistry(store).save(spec)
        calculation = Calculation(
            calculation_id="calc-current",
            covenant_id="COV-CURRENT",
            borrower_ids=["B-CURRENT"],
            metric_type="sum",
            sql="SELECT SUM(amount) FROM transactions",
            parameter_summary=["B-CURRENT"],
            input_row_count=2,
            value=Decimal("8000000"),
            unit="KZT",
            created_at=datetime(2026, 4, 30, tzinfo=UTC),
        )
        store.connection.execute(
            "INSERT INTO calculations VALUES (?, ?, ?, CAST(? AS JSON))",
            [
                calculation.calculation_id,
                calculation.covenant_id,
                "B-CURRENT",
                calculation.model_dump_json(),
            ],
        )
        result = CovenantResult(
            borrower_id="B-CURRENT",
            covenant_id="COV-CURRENT",
            verdict="complied",
            number=Decimal("8000000"),
            number_unit="KZT",
            calculation_id="calc-current",
            status="success",
        )
        batch = BatchEvaluationReport(
            run_id="eval-current",
            evaluation_date=date(2026, 4, 30),
            expected_pair_count=1,
            actual_pair_count=1,
            results=[result],
            verification=VerificationReport(
                valid=True,
                expected_pair_count=1,
                actual_pair_count=1,
                issues=[],
            ),
        )
        corpus = [
            SimilarReviewCase(
                case_id="gold-foreign",
                question="Did another borrower exceed its monthly outgoing KZT limit?",
                metric_type="sum",
                answer=CovenantResult(
                    borrower_id="B-FOREIGN",
                    covenant_id="COV-FOREIGN",
                    verdict="violated",
                    number=Decimal("16000000"),
                    evidence_transaction_id="TX-FOREIGN",
                    status="success",
                ),
                rationale="16M > 15M",
                embedding_text="monthly outgoing KZT limit",
            )
        ]
        reviewer = CopyingReviewer()
        embedder = ConstantEmbedder()

        reviewed = run_review(
            batch=batch,
            store=store,
            reviewer=reviewer,
            embedder=embedder,
            corpus=corpus,
        ).reviewed_results[0]

        assert embedder.calls > 0
        assert reviewed.review_status == "invalid_reviewer_output"
        assert reviewed.result.number == Decimal("8000000")
        assert reviewed.result.verdict == "complied"
        assert reviewed.result.evidence_transaction_id is None
        assert reviewed.similarity_scores["gold-foreign"] == 1.0
