from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from halyk_covenants.review.models import ReviewCase, SimilarityMatch

SYSTEM_PROMPT = """You are a verification reviewer for covenant answers.
Check whether the CURRENT ANSWER is supported by the supplied deterministic rationale and covenant.
Do not recalculate from unstated data. Do not invent a new numeric value. Do not copy borrower IDs,
thresholds, numbers, verdicts, or transaction IDs from similar examples into the current case.
Similar examples are only reasoning-pattern references. The current numeric result can only come
from the current deterministic calculation. Return a concise evidence rationale, not hidden
chain-of-thought. If evidence is insufficient, set accepted=false and lower confidence."""


def review_messages(
    case: ReviewCase,
    *,
    similar_cases: list[SimilarityMatch] | None = None,
) -> list[object]:
    similar_payload = [
        {
            "similarity": match.similarity,
            "case_id": match.case.case_id,
            "question": match.case.question,
            "answer": match.case.answer.model_dump(mode="json"),
            "rationale": match.case.rationale,
            "metric_type": match.case.metric_type,
        }
        for match in similar_cases or []
    ]
    payload = {
        "current": {
            "question": case.question,
            "answer": case.answer.model_dump(mode="json"),
            "rationale": case.rationale,
            "covenant": case.covenant.model_dump(mode="json"),
            "verification_issues": case.verification_issues,
            "compiler_confidence": (
                str(case.compiler_confidence) if case.compiler_confidence is not None else None
            ),
        },
        "similar_validated_cases": similar_payload,
    }
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                "Review the current answer. Preserve the current deterministic number and only "
                "use the current evidence transaction (or null). Confidence must be between 0 "
                "and 1.\n\n"
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
        ),
    ]
