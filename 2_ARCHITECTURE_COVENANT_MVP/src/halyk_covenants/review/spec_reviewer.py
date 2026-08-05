from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.llm.prompts.spec_review import (
    context_grade_messages,
    spec_review_messages,
)
from halyk_covenants.observability import trace_stage
from halyk_covenants.review.spec_models import ContextGrade, SpecReviewDecision


class SpecReviewer(Protocol):
    model_name: str
    prompt_version: str

    def review_spec(self, spec: CovenantSpec, context: str = "") -> SpecReviewDecision: ...

    def grade_context(
        self, spec: CovenantSpec, context: str, objection: str
    ) -> ContextGrade: ...


class LangChainSpecReviewer:
    prompt_version = "spec-review-v2"

    def __init__(self, model: Any) -> None:
        self.model = model
        self.review_model = model.with_structured_output(SpecReviewDecision, method="json_mode")
        self.grade_model = model.with_structured_output(ContextGrade, method="json_mode")
        self.model_name = str(
            getattr(model, "model_name", getattr(model, "model", type(model).__name__))
        )

    @trace_stage("review.spec_llm", run_type="llm", tags=("review", "spec", "llm"))
    def review_spec(self, spec: CovenantSpec, context: str = "") -> SpecReviewDecision:
        raw = self.review_model.invoke(spec_review_messages(spec, context))
        return _coerce(raw, SpecReviewDecision)

    @trace_stage("review.context_grade_llm", run_type="llm", tags=("review", "rag", "llm"))
    def grade_context(self, spec: CovenantSpec, context: str, objection: str) -> ContextGrade:
        raw = self.grade_model.invoke(context_grade_messages(spec, context, objection))
        return _coerce(raw, ContextGrade)


def _coerce(raw: Any, model_type: type[BaseModel]) -> Any:
    if isinstance(raw, model_type):
        return raw
    if isinstance(raw, BaseModel):
        return model_type.model_validate(raw.model_dump(mode="python"))
    return model_type.model_validate(raw)
