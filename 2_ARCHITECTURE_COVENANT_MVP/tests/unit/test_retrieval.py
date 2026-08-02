from halyk_covenants.documents.retrieval import HybridRetriever
from halyk_covenants.domain import DocumentBlock, SourceRef


class LiteralEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = {
            "Permitted Payments means operating and tax payments.": [1.0, 0.0],
            "Tax payments are excluded from the outgoing limit.": [0.9, 0.1],
            "The office address is Almaty.": [0.0, 1.0],
            "Permitted Payments means anything.": [1.0, 0.0],
            "Permitted Payments tax exception": [1.0, 0.0],
        }
        return [vectors[text] for text in texts]


def block(block_id: str, text: str) -> DocumentBlock:
    source = SourceRef(document_id="DOC1", page=1)
    return DocumentBlock(
        block_id=block_id,
        document_id="DOC1",
        page=1,
        block_type="text",
        text=text,
        extraction_method="native",
        confidence=1,
        source=source,
    )


def test_definition_and_exception_rank_above_unrelated_blocks() -> None:
    retriever = HybridRetriever(embedder=LiteralEmbedder())
    retriever.index(
        [
            block("definition", "Permitted Payments means operating and tax payments."),
            block("exception", "Tax payments are excluded from the outgoing limit."),
            block("address", "The office address is Almaty."),
        ]
    )

    results = retriever.search("Permitted Payments tax exception", document_id="DOC1", k=2)

    assert [item.block.block_id for item in results] == ["definition", "exception"]


def test_document_filter_excludes_other_documents() -> None:
    other = block("other", "Permitted Payments means anything.").model_copy(
        update={"document_id": "DOC2", "source": SourceRef(document_id="DOC2", page=1)}
    )
    retriever = HybridRetriever(embedder=LiteralEmbedder())
    retriever.index(
        [block("definition", "Permitted Payments means operating and tax payments."), other]
    )

    results = retriever.search("Permitted Payments tax exception", document_id="DOC1", k=5)

    assert [item.block.block_id for item in results] == ["definition"]
