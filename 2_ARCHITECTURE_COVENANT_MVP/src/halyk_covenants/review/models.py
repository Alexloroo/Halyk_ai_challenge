from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.domain import Calculation, CovenantResult, CovenantSpec

ReviewStatus = Literal[
    "accepted",
    "accepted_after_similarity",
    "low_confidence",
    "invalid_reviewer_output",
    "review_failed",
]


class ReviewCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    borrower_id: str
    covenant_id: str
    evaluation_date: date
    question: str
    answer: CovenantResult
    rationale: str
    covenant: CovenantSpec
    calculation: Calculation | None = None
    verification_issues: list[str] = Field(default_factory=list)
    compiler_confidence: Decimal | None = Field(default=None, ge=0, le=1)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    confidence: Decimal = Field(ge=0, le=1)
    verdict: Literal["complied", "violated", "unknown"]
    number: Decimal | int | None = None
    evidence_transaction_id: str | None = None
    rationale: str
    issues: list[str] = Field(default_factory=list)
    used_similarity_fallback: bool = False
    similar_case_ids: list[str] = Field(default_factory=list)


class ReviewedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: CovenantResult
    review: ReviewDecision
    review_status: ReviewStatus
    fallback_reasons: list[str] = Field(default_factory=list)
    similarity_scores: dict[str, float] = Field(default_factory=dict)


class SimilarReviewCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str
    covenant_type: str | None = None
    metric_type: str | None = None
    answer: CovenantResult
    rationale: str
    embedding_text: str


class SimilarityMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: SimilarReviewCase
    similarity: float = Field(ge=-1, le=1)
