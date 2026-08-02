from __future__ import annotations

from decimal import Decimal
from typing import Any

from halyk_covenants.observability import trace_stage

from .models import SubmissionProfile, SubmissionValidationReport


class SubmissionValidator:
    def __init__(self, profile: SubmissionProfile) -> None:
        self.profile = profile

    @trace_stage("submission.validate", run_type="tool", tags=("submission", "verification"))
    def validate(self, payload: dict[str, Any]) -> SubmissionValidationReport:
        errors: list[str] = []
        root_keys = set(payload)
        if root_keys != {self.profile.answers_key}:
            unexpected = sorted(root_keys - {self.profile.answers_key})
            missing = sorted({self.profile.answers_key} - root_keys)
            if unexpected:
                errors.append(f"unexpected root keys: {', '.join(unexpected)}")
            if missing:
                errors.append(f"missing root keys: {', '.join(missing)}")
            return SubmissionValidationReport(valid=False, errors=errors)

        answers = payload[self.profile.answers_key]
        if not isinstance(answers, list):
            return SubmissionValidationReport(valid=False, errors=["answers must be a list"])
        seen: set[tuple[str, str]] = set()
        allowed_verdicts = set(self.profile.verdict_labels.values())
        for index, answer in enumerate(answers):
            if not isinstance(answer, dict):
                errors.append(f"answer[{index}] must be an object")
                continue
            if set(answer) != self.profile.answer_keys:
                errors.append(f"answer[{index}] keys do not match the strict profile")
                continue
            pair = (answer[self.profile.borrower_key], answer[self.profile.covenant_key])
            if not all(isinstance(value, str) and value for value in pair):
                errors.append(f"answer[{index}] borrower/covenant identifiers must be strings")
            elif pair in seen:
                errors.append(f"answer[{index}] duplicates pair {pair}")
            else:
                seen.add(pair)
            if answer[self.profile.verdict_key] not in allowed_verdicts:
                errors.append(f"answer[{index}] has an invalid verdict")
            number = answer[self.profile.number_key]
            if number is None and not self.profile.allow_null_number:
                errors.append(f"answer[{index}] null number is forbidden")
            elif number is not None:
                if not isinstance(number, str):
                    errors.append(f"answer[{index}] number must be a decimal string or null")
                else:
                    try:
                        Decimal(number)
                    except Exception:
                        errors.append(f"answer[{index}] number is not decimal")
        return SubmissionValidationReport(valid=not errors, errors=errors)
