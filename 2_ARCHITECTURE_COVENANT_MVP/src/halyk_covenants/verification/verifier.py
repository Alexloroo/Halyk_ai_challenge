from __future__ import annotations

from halyk_covenants.domain import CovenantResult, CovenantSpec, FailureStage
from halyk_covenants.evaluators import compare
from halyk_covenants.observability import annotate_current_trace, trace_stage

from .models import PairVerification, VerificationIssue, VerificationReport


class ResultVerifier:
    @trace_stage(
        "verification.pair",
        run_type="tool",
        tags=("verification", "deterministic"),
        failure_stage=FailureStage.VERIFICATION,
    )
    def verify_pair(self, spec: CovenantSpec, result: CovenantResult) -> PairVerification:
        issues: list[VerificationIssue] = []
        if result.number is not None and spec.condition.threshold is not None:
            expected_verdict = (
                "complied"
                if compare(result.number, spec.condition.comparator, spec.condition.threshold)
                else "violated"
            )
            if result.verdict != expected_verdict:
                issues.append(
                    VerificationIssue(
                        code="verdict_mismatch",
                        message=(
                            f"stored verdict {result.verdict} differs from deterministic "
                            f"verdict {expected_verdict}"
                        ),
                        classification="non_repairable",
                        borrower_id=result.borrower_id,
                        covenant_id=result.covenant_id,
                    )
                )
        elif result.status == "success":
            issues.append(
                VerificationIssue(
                    code="missing_number",
                    message="successful result has no numeric metric",
                    classification="non_repairable",
                    borrower_id=result.borrower_id,
                    covenant_id=result.covenant_id,
                )
            )
        if issues:
            annotate_current_trace(
                metadata={
                    "failure_stage": FailureStage.VERIFICATION.value,
                    "verification_issue_codes": [issue.code for issue in issues],
                },
                tags=(FailureStage.VERIFICATION.value,),
            )
        return PairVerification(valid=not issues, issues=issues)

    @trace_stage(
        "verification.completeness",
        run_type="chain",
        tags=("verification",),
        failure_stage=FailureStage.VERIFICATION,
    )
    def verify(
        self,
        expected_pairs: list[tuple[str, str]],
        results: list[CovenantResult],
    ) -> VerificationReport:
        issues: list[VerificationIssue] = []
        actual = {(result.borrower_id, result.covenant_id): result for result in results}
        expected = set(expected_pairs)
        for borrower_id, covenant_id in sorted(expected - set(actual)):
            issues.append(
                VerificationIssue(
                    code="missing_result",
                    message="expected borrower/covenant pair is absent",
                    classification="non_repairable",
                    borrower_id=borrower_id,
                    covenant_id=covenant_id,
                )
            )
        for key, result in sorted(actual.items()):
            if key not in expected:
                issues.append(
                    VerificationIssue(
                        code="unexpected_result",
                        message="result does not occur in the expected completeness matrix",
                        classification="non_repairable",
                        borrower_id=key[0],
                        covenant_id=key[1],
                    )
                )
            if result.status == "failed":
                issues.append(
                    VerificationIssue(
                        code="failed_result",
                        message="pair was preserved but evaluation failed",
                        classification="repairable",
                        borrower_id=key[0],
                        covenant_id=key[1],
                    )
                )
            elif result.status == "partial":
                issues.append(
                    VerificationIssue(
                        code="partial_result",
                        message=(
                            "pair contains useful scored components but at least one component "
                            "requires repair"
                        ),
                        classification="repairable",
                        borrower_id=key[0],
                        covenant_id=key[1],
                    )
                )
        if issues:
            annotate_current_trace(
                metadata={
                    "failure_stage": FailureStage.VERIFICATION.value,
                    "verification_issue_count": len(issues),
                    "verification_issue_codes": sorted({issue.code for issue in issues}),
                },
                tags=(FailureStage.VERIFICATION.value,),
            )
        return VerificationReport(
            valid=not issues,
            expected_pair_count=len(expected),
            actual_pair_count=len(actual),
            issues=issues,
        )
