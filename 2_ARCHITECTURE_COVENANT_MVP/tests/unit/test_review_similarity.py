from decimal import Decimal

from halyk_covenants.domain import CovenantResult
from halyk_covenants.review import SimilarReviewCase
from halyk_covenants.review.similarity import SimilarityRetriever, cosine_similarity


class FakeEmbedder:
    model_name = "fake"

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [self.vectors[text] for text in texts]


def case(case_id: str, embedding_text: str | None) -> SimilarReviewCase:
    return SimilarReviewCase(
        case_id=case_id,
        question=f"question {case_id}",
        covenant_type="financial",
        metric_type="sum",
        answer=CovenantResult(
            borrower_id=case_id,
            covenant_id="COV",
            verdict="complied",
            number=Decimal("1"),
            status="success",
        ),
        rationale="validated",
        embedding_text=embedding_text,
    )


def test_cosine_similarity_handles_zero_vector() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_similarity_rejects_dimension_mismatch() -> None:
    try:
        cosine_similarity([1.0], [1.0, 0.0])
    except ValueError as exc:
        assert "dimensions" in str(exc)
    else:
        raise AssertionError("dimension mismatch must fail")


def test_similarity_ties_are_ordered_by_case_id_and_respect_top_k() -> None:
    embedder = FakeEmbedder(
        {
            "query": [1.0, 0.0],
            "text-a": [1.0, 0.0],
            "text-b": [1.0, 0.0],
            "text-c": [0.0, 1.0],
        }
    )
    retriever = SimilarityRetriever(
        [case("B", "text-b"), case("A", "text-a"), case("C", "text-c")],
        embedder,
    )

    matches = retriever.search("query", k=2, minimum_similarity=0)

    assert [item.case.case_id for item in matches] == ["A", "B"]
    assert [item.similarity for item in matches] == [1.0, 1.0]


def test_similarity_filters_below_threshold() -> None:
    embedder = FakeEmbedder({"query": [1.0, 0.0], "close": [1.0, 0.0], "far": [0.0, 1.0]})
    retriever = SimilarityRetriever([case("A", "close"), case("B", "far")], embedder)

    matches = retriever.search("query", k=5, minimum_similarity=0.55)

    assert [item.case.case_id for item in matches] == ["A"]


def test_similarity_embeds_question_when_custom_text_is_missing() -> None:
    embedder = FakeEmbedder({"current question": [1.0, 0.0], "question A": [1.0, 0.0]})
    retriever = SimilarityRetriever([case("A", None)], embedder)

    matches = retriever.search("current question", k=1, minimum_similarity=0)

    assert [item.case.case_id for item in matches] == ["A"]
    assert embedder.calls[0] == ["question A"]
