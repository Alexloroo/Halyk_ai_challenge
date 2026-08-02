from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VerificationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    classification: Literal["repairable", "non_repairable"]
    borrower_id: str | None = None
    covenant_id: str | None = None


class PairVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    issues: list[VerificationIssue] = Field(default_factory=list)


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    expected_pair_count: int = Field(ge=0)
    actual_pair_count: int = Field(ge=0)
    issues: list[VerificationIssue] = Field(default_factory=list)
