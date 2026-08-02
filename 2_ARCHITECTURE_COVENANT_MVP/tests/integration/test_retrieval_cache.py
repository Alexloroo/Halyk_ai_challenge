from pathlib import Path

from halyk_covenants.documents.retrieval import HybridRetriever
from halyk_covenants.domain import DocumentBlock, SourceRef
from halyk_covenants.storage.artifact_store import ArtifactStore


class CountingEmbedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[float(len(text)), 1.0] for text in texts]


def test_unchanged_blocks_are_not_reembedded_across_retriever_instances(tmp_path: Path) -> None:
    source = SourceRef(document_id="DOC1", page=1)
    block = DocumentBlock(
        block_id="B1",
        document_id="DOC1",
        page=1,
        block_type="text",
        text="Monthly outgoing limit",
        extraction_method="native",
        confidence=1,
        source=source,
    )
    store = ArtifactStore(tmp_path)
    first = CountingEmbedder()
    HybridRetriever(embedder=first, artifact_store=store).index([block])
    second = CountingEmbedder()
    HybridRetriever(embedder=second, artifact_store=store).index([block])

    assert first.texts == ["Monthly outgoing limit"]
    assert second.texts == []
