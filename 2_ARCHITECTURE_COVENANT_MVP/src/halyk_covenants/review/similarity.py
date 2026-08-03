from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np

from halyk_covenants.observability import trace_stage
from halyk_covenants.review.models import SimilarityMatch, SimilarReviewCase


class ReviewEmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError("embedding dimensions must match")
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0:
        return 0.0
    return float(np.dot(a, b) / denominator)


class SimilarityRetriever:
    def __init__(
        self,
        cases: list[SimilarReviewCase],
        embedder: ReviewEmbeddingProvider,
    ) -> None:
        self.cases = list(cases)
        self.embedder = embedder
        self._case_vectors: dict[str, list[float]] = {}

    @trace_stage("review.similarity.embed", run_type="embedding", tags=("review", "similarity"))
    def _embed_corpus(self) -> None:
        missing = [case for case in self.cases if case.case_id not in self._case_vectors]
        if not missing:
            return
        texts = [case.embedding_text or case.question for case in missing]
        vectors = self.embedder.embed(texts)
        if len(vectors) != len(missing):
            raise ValueError("embedding provider returned an unexpected vector count")
        for case, vector in zip(missing, vectors, strict=True):
            self._case_vectors[case.case_id] = vector

    @trace_stage("review.similarity.search", run_type="retriever", tags=("review", "similarity"))
    def search(
        self,
        query_text: str,
        *,
        k: int = 5,
        minimum_similarity: float = 0.55,
    ) -> list[SimilarityMatch]:
        if k <= 0 or not self.cases:
            return []
        self._embed_corpus()
        query_vector = self.embedder.embed([query_text])[0]
        matches = [
            SimilarityMatch(
                case=case,
                similarity=cosine_similarity(query_vector, self._case_vectors[case.case_id]),
            )
            for case in self.cases
        ]
        matches = [match for match in matches if match.similarity >= minimum_similarity]
        matches.sort(key=lambda match: (-match.similarity, match.case.case_id))
        return matches[:k]
