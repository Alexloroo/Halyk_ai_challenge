from __future__ import annotations

from decimal import Decimal

from halyk_covenants.domain import CovenantResult
from halyk_covenants.observability import trace_stage

from .models import SubmissionProfile


class SubmissionSerializer:
    def __init__(self, profile: SubmissionProfile) -> None:
        self.profile = profile

    @trace_stage("submission.serialize", run_type="tool", tags=("submission",))
    def serialize(self, results: list[CovenantResult]) -> dict[str, object]:
        answers: list[dict[str, object]] = []
        for result in sorted(results, key=lambda item: (item.borrower_id, item.covenant_id)):
            answer: dict[str, object] = {
                self.profile.borrower_key: result.borrower_id,
                self.profile.covenant_key: result.covenant_id,
                self.profile.verdict_key: self.profile.verdict_labels[result.verdict],
                self.profile.number_key: self._number(result),
            }
            if self.profile.include_evidence:
                answer[self.profile.evidence_key] = result.evidence_transaction_id
            answers.append(answer)
        return {self.profile.answers_key: answers}

    def _number(self, result: CovenantResult) -> str | None:
        if result.number is None:
            if not self.profile.allow_null_number:
                raise ValueError(
                    f"null number is forbidden for {result.borrower_id}/{result.covenant_id}"
                )
            return None
        value = Decimal(result.number)
        if (
            result.number_unit in {"ratio", "fraction"}
            and self.profile.ratio_representation == "percentage"
        ):
            value *= Decimal(100)
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered or "0"
