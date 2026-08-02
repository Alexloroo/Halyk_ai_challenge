from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SubmissionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    answers_key: str = "answers"
    borrower_key: str = "borrower_id"
    covenant_key: str = "covenant_id"
    verdict_key: str = "verdict"
    number_key: str = "number"
    evidence_key: str = "evidence_transaction_id"
    ratio_representation: Literal["fraction", "percentage"] = "fraction"
    verdict_labels: dict[str, str] = Field(
        default_factory=lambda: {
            "complied": "complied",
            "violated": "violated",
            "unknown": "unknown",
        }
    )
    include_evidence: bool = True
    allow_null_number: bool = True

    @model_validator(mode="after")
    def validate_verdict_labels(self) -> SubmissionProfile:
        required = {"complied", "violated", "unknown"}
        if set(self.verdict_labels) != required:
            raise ValueError(f"verdict_labels must contain exactly {sorted(required)}")
        return self

    @property
    def answer_keys(self) -> set[str]:
        keys = {self.borrower_key, self.covenant_key, self.verdict_key, self.number_key}
        if self.include_evidence:
            keys.add(self.evidence_key)
        return keys


class SubmissionValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str] = Field(default_factory=list)
