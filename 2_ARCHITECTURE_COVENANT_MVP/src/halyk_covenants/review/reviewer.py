from __future__ import annotations

from typing import Protocol

from halyk_covenants.review.models import ReviewCase, ReviewDecision, SimilarityMatch


class Reviewer(Protocol):
    model_name: str
    prompt_version: str

    def review(
        self,
        case: ReviewCase,
        *,
        similar_cases: list[SimilarityMatch] | None = None,
    ) -> ReviewDecision: ...
