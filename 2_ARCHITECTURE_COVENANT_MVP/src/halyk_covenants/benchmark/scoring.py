from decimal import Decimal

from halyk_covenants.benchmark.models import BenchmarkSummary, CaseScore
from halyk_covenants.domain import CovenantResult
from halyk_covenants.synthetic.models import ExpectedAnswer


def _numbers_match(expected: Decimal | int | None, actual: Decimal | int | None) -> bool:
    if expected is None or actual is None:
        return expected is actual
    return Decimal(expected) == Decimal(actual)


def score_answer(
    case_id: str,
    expected: ExpectedAnswer,
    actual: CovenantResult,
) -> CaseScore:
    number_score = int(_numbers_match(expected.number, actual.number))
    verdict_score = int(expected.verdict == actual.verdict)
    evidence_score = int(expected.evidence_transaction_id == actual.evidence_transaction_id)
    component_score = number_score + verdict_score + evidence_score
    return CaseScore(
        case_id=case_id,
        number_score=number_score,
        verdict_score=verdict_score,
        evidence_score=evidence_score,
        component_score=component_score,
        full_exact_match=component_score == 3,
        status_match=expected.status == actual.status,
    )


def summarize_scores(scores: list[CaseScore]) -> BenchmarkSummary:
    if not scores:
        raise ValueError("cannot summarize an empty benchmark")
    total = len(scores)
    total_decimal = Decimal(total)
    earned = sum(score.component_score for score in scores)
    return BenchmarkSummary(
        total_cases=total,
        earned_components=earned,
        maximum_components=total * 3,
        number_accuracy=Decimal(sum(score.number_score for score in scores)) / total_decimal,
        verdict_accuracy=Decimal(sum(score.verdict_score for score in scores)) / total_decimal,
        evidence_accuracy=Decimal(sum(score.evidence_score for score in scores)) / total_decimal,
        component_accuracy=Decimal(earned) / Decimal(total * 3),
        full_exact_match_accuracy=(
            Decimal(sum(score.full_exact_match for score in scores)) / total_decimal
        ),
        failed_case_ids=[score.case_id for score in scores if not score.full_exact_match],
    )

