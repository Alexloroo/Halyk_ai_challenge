from decimal import Decimal

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantResult,
    CovenantSpec,
    MetricSpec,
    SourceRef,
)
from halyk_covenants.verification import ResultVerifier


def spec(covenant_id: str = "C1") -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        raw_text="Limit <= 10",
        borrower_ids=["B1"],
        metric=MetricSpec(metric_type="sum", field="amount"),
        condition=ConditionSpec(comparator="<=", threshold=10),
        source=SourceRef(document_id="d1", page=1),
        confidence=1,
    )


def test_verifier_detects_number_verdict_mismatch_as_non_repairable() -> None:
    result = CovenantResult(
        borrower_id="B1",
        covenant_id="C1",
        verdict="complied",
        number=Decimal("11"),
        status="success",
    )

    verification = ResultVerifier().verify_pair(spec(), result)

    assert verification.valid is False
    assert verification.issues[0].code == "verdict_mismatch"
    assert verification.issues[0].classification == "non_repairable"


def test_verifier_reports_missing_expected_pair() -> None:
    report = ResultVerifier().verify(
        expected_pairs=[("B1", "C1"), ("B1", "C2")],
        results=[
            CovenantResult(
                borrower_id="B1",
                covenant_id="C1",
                verdict="complied",
                number=Decimal("10"),
                status="success",
            )
        ],
    )

    assert report.expected_pair_count == 2
    assert report.actual_pair_count == 1
    assert [(issue.borrower_id, issue.covenant_id) for issue in report.issues] == [("B1", "C2")]
    assert report.issues[0].code == "missing_result"


def test_failed_result_is_preserved_but_reported() -> None:
    result = CovenantResult(
        borrower_id="B1",
        covenant_id="C1",
        verdict="unknown",
        status="failed",
        errors=["unsupported"],
    )

    report = ResultVerifier().verify([("B1", "C1")], [result])

    assert report.actual_pair_count == 1
    assert report.issues[0].code == "failed_result"
