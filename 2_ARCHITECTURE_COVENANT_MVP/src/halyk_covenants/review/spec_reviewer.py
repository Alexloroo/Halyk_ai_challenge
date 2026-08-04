from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.llm.prompts.spec_review import spec_review_messages
from halyk_covenants.observability import trace_stage
from halyk_covenants.review.spec_models import SpecReviewDecision


class SpecReviewer(Protocol):
    model_name: str
    prompt_version: str

    def review_spec(self, spec: CovenantSpec) -> SpecReviewDecision: ...


class LangChainSpecReviewer:
    prompt_version = "spec-review-v1"

    def __init__(self, model: Any) -> None:
        self.model = model
        self.structured_model = model.with_structured_output(
            SpecReviewDecision, method="json_mode"
        )
        self.model_name = str(
            getattr(model, "model_name", getattr(model, "model", type(model).__name__))
        )

    @trace_stage("review.spec_llm", run_type="llm", tags=("review", "spec", "llm"))
    def review_spec(self, spec: CovenantSpec) -> SpecReviewDecision:
        raw = self.structured_model.invoke(spec_review_messages(spec))
        if isinstance(raw, SpecReviewDecision):
            return raw
        if isinstance(raw, BaseModel):
            return SpecReviewDecision.model_validate(raw.model_dump(mode="python"))
        return SpecReviewDecision.model_validate(raw)
