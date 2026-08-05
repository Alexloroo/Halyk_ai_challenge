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


class ContextGrade(BaseModel):
    """Whether the retrieved context contains what is needed to fix an objection.

    Separates two causes of a wrong specification:
      - the model misread context it had        -> sufficient=True,  recompile can fix it
      - the context never contained the answer  -> sufficient=False, recompile is futile

    `missing_query` becomes the search query for one bounded retrieval expansion.
    """

    model_config = ConfigDict(extra="forbid")

    sufficient: bool
    missing_query: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None
