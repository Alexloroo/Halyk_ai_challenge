from datetime import date
from decimal import Decimal

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantResult,
    CovenantSpec,
    EvidenceMode,
    FilterSpec,
    MetricSpec,
    SourceRef,
    TimeWindowSpec,
)
from halyk_covenants.evals import (
    score_compiler_output,
    score_covenant_detection,
    score_covenant_result,
)


def _spec(*, threshold: str = "15000000", comparator: str = "<=") -> CovenantSpec:
    return CovenantSpec(
        covenant_id="COV-A1",
        raw_text="Monthly outgoing payments must not exceed 15M KZT.",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="sum", field="amount", unit="KZT"),
        condition=ConditionSpec(
            comparator=comparator,  # type: ignore[arg-type]
            threshold=Decimal(threshold),
            unit="KZT",
            currency="KZT",
        ),
        transaction_filters=[FilterSpec(field="direction", operator="eq", value="outgoing")],
        time_window=TimeWindowSpec(type="calendar_month"),
        evidence_mode=EvidenceMode.NONE,
        effective_from=date(2026, 1, 1),
        source=SourceRef(document_id="doc.pdf", page=1),
        confidence=1,
    )


def test_covenant_result_scores_three_hackathon_components_independently() -> None:
    expected = CovenantResult(
        borrower_id="B001",
        covenant_id="COV-A1",
        verdict="violated",
        number=Decimal("16000000"),
        number_unit="KZT",
        evidence_transaction_id=None,
        status="success",
    )
    actual = expected.model_copy(update={"verdict": "complied"})

    scores = score_covenant_result(expected, actual)

    assert scores == {
        "number_exact": 1,
        "verdict_exact": 0,
        "evidence_exact": 1,
        "component_score": 2,
        "full_exact_match": 0,
    }


def test_compiler_score_exposes_field_level_bottlenecks() -> None:
    expected = _spec()
    actual = _spec(threshold="1500000")

    scores = score_compiler_output(expected, actual)

    assert scores["metric_type_exact"] == 1
    assert scores["field_exact"] == 1
    assert scores["filters_exact"] == 1
    assert scores["period_exact"] == 1
    assert scores["comparator_exact"] == 1
    assert scores["threshold_exact"] == 0
    assert scores["currency_exact"] == 1
    assert scores["borrower_scope_exact"] == 1
    assert scores["evidence_mode_exact"] == 1


def test_detection_score_prioritizes_recall_and_reports_precision() -> None:
    scores = score_covenant_detection(
        expected_ids=["COV-1", "COV-2", "COV-3"],
        actual_ids=["COV-1", "COV-3", "COV-X"],
    )

    assert scores["true_positive"] == 2
    assert scores["false_negative"] == 1
    assert scores["false_positive"] == 1
    assert scores["recall"] == Decimal("0.6666666666666666666666666667")
    assert scores["precision"] == Decimal("0.6666666666666666666666666667")
