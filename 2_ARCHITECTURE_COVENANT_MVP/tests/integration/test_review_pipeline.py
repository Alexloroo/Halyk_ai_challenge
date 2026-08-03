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
from halyk_covenants.review import ReviewCase, ReviewDecision, ReviewService
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.verification import VerificationReport


class AcceptReviewer:
    model_name = "fake"
    prompt_version = "test"

    def review(self, case: ReviewCase, *, similar_cases=None) -> ReviewDecision:
        del similar_cases
        return ReviewDecision(
            accepted=True,
            confidence=Decimal("0.90"),
            verdict=case.answer.verdict,
            number=case.answer.number,
            evidence_transaction_id=case.answer.evidence_transaction_id,
            rationale="supported",
        )


class NoSimilarity:
    def search(self, query_text: str, *, k: int, minimum_similarity: float):
        raise AssertionError(
            f"similarity must not be used: {query_text=} {k=} {minimum_similarity=}"
        )


def test_review_pipeline_persists_review_without_mutating_result() -> None:
    from halyk_covenants.pipeline.review import ReviewPipeline

    with DuckDBStore(":memory:") as store:
        spec = CovenantSpec(
            covenant_id="COV-1",
            raw_text="Monthly outgoing KZT must not exceed 15M KZT",
            borrower_ids=["B001"],
            metric=MetricSpec(metric_type="sum", field="amount", unit="KZT"),
            condition=ConditionSpec(
                comparator="<=", threshold=Decimal("15000000"), currency="KZT"
            ),
            source=SourceRef(document_id="DOC-1", page=1),
            confidence=0.95,
        )
        CovenantRegistry(store).save(spec)
        calculation = Calculation(
            calculation_id="calc-1",
            covenant_id="COV-1",
            borrower_ids=["B001"],
            metric_type="sum",
            sql="SELECT SUM(amount) FROM transactions",
            parameter_summary=["B001"],
            input_row_count=3,
            value=Decimal("16000000"),
            unit="KZT",
            created_at=datetime(2026, 4, 30, tzinfo=UTC),
        )
        store.connection.execute(
            "INSERT INTO calculations VALUES (?, ?, ?, CAST(? AS JSON))",
            ["calc-1", "COV-1", "B001", calculation.model_dump_json()],
        )
        result = CovenantResult(
            borrower_id="B001",
            covenant_id="COV-1",
            verdict="violated",
            number=Decimal("16000000"),
            number_unit="KZT",
            calculation_id="calc-1",
            status="success",
        )
        batch = BatchEvaluationReport(
            run_id="eval-run-1",
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
        service = ReviewService(reviewer=AcceptReviewer(), similarity_retriever=NoSimilarity())

        report = ReviewPipeline(store, service=service).run(batch)

        assert report.reviewed_results[0].review_status == "accepted"
        assert report.reviewed_results[0].result == result
        row = store.connection.execute(
            "SELECT status, decision_json FROM review_decisions"
        ).fetchone()
        assert row[0] == "accepted"
        assert "16000000" in row[1]
