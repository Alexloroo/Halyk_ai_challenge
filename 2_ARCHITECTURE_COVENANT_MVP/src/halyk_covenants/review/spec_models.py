from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SpecReviewDecision(BaseModel):
    """Decision from the spec reviewer — structurally safe.

    No number, verdict, or evidence fields exist in this type.
    pydantic's extra="forbid" rejects any attempt by the LLM to add them.
    """

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    confidence: float = Field(ge=0.0, le=1.0)
    objection: str | None = None
    issues: list[str] = Field(default_factory=list)
