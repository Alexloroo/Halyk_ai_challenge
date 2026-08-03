from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from halyk_covenants.llm.prompts import review_messages
from halyk_covenants.observability import trace_stage
from halyk_covenants.review.models import ReviewCase, ReviewDecision, SimilarityMatch


class LangChainReviewer:
    prompt_version = "review-v1"

    def __init__(self, model: Any) -> None:
        self.model = model
        self.structured_model = model.with_structured_output(ReviewDecision, method="json_mode")
        self.model_name = str(
            getattr(model, "model_name", getattr(model, "model", type(model).__name__))
        )

    @trace_stage("review.llm", run_type="llm", tags=("review", "llm"))
    def review(
        self,
        case: ReviewCase,
        *,
        similar_cases: list[SimilarityMatch] | None = None,
    ) -> ReviewDecision:
        raw = self.structured_model.invoke(
            review_messages(case, similar_cases=similar_cases)
        )
        if isinstance(raw, ReviewDecision):
            return raw
        if isinstance(raw, BaseModel):
            return ReviewDecision.model_validate(raw.model_dump(mode="python"))
        return ReviewDecision.model_validate(raw)
