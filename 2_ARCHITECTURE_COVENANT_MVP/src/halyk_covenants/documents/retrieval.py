from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict
from rank_bm25 import BM25Okapi

from halyk_covenants.domain import DocumentBlock
from halyk_covenants.observability import trace_stage
from halyk_covenants.storage.artifact_store import ArtifactStore

TOKEN_PATTERN = re.compile(r"[\w]+", flags=re.UNICODE)


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class RetrievedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block: DocumentBlock
    lexical_score: float
    semantic_score: float
    score: float


class IndexStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_count: int
    embedded_count: int
    cache_hit_count: int


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_name: str = "intfloat/multilingual-e5-small") -> None:
        self.model_name = model_name
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError("install the semantic extra to use local embeddings") from exc
            self._model = SentenceTransformer(self.model_name)
        values = self._model.encode(texts, normalize_embeddings=True)
        return [[float(item) for item in vector] for vector in values]


class HybridRetriever:
    def __init__(
        self,
        *,
        embedder: EmbeddingProvider | None = None,
        artifact_store: ArtifactStore | None = None,
        lexical_weight: float = 0.45,
        semantic_weight: float = 0.55,
    ) -> None:
        if lexical_weight < 0 or semantic_weight < 0 or lexical_weight + semantic_weight == 0:
            raise ValueError("retrieval weights must be non-negative and not both zero")
        self.embedder = embedder
        self.artifact_store = artifact_store
        if embedder is None:
            self.lexical_weight = 1.0
            self.semantic_weight = 0.0
        else:
            total = lexical_weight + semantic_weight
            self.lexical_weight = lexical_weight / total
            self.semantic_weight = semantic_weight / total
        self.blocks: list[DocumentBlock] = []
        self.vectors = np.empty((0, 0), dtype=np.float64)
        self.bm25: BM25Okapi | None = None

    @trace_stage("retrieval.index", run_type="retriever")
    def index(self, blocks: list[DocumentBlock]) -> IndexStats:
        self.blocks = list(blocks)
        self.bm25 = (
            BM25Okapi([self._tokenize(block.text) for block in self.blocks]) if blocks else None
        )
        if self.embedder is None:
            self.vectors = np.empty((len(self.blocks), 0), dtype=np.float64)
            return IndexStats(block_count=len(blocks), embedded_count=0, cache_hit_count=0)

        vectors: list[list[float] | None] = []
        missing_texts: list[str] = []
        missing_positions: list[int] = []
        cache_hits = 0
        for position, block in enumerate(self.blocks):
            key = self._embedding_key(block.text)
            cached = self.artifact_store.get_embedding(key) if self.artifact_store else None
            vectors.append(cached)
            if cached is None:
                missing_texts.append(block.text)
                missing_positions.append(position)
            else:
                cache_hits += 1
        if missing_texts:
            embedded = self.embedder.embed(missing_texts)
            if len(embedded) != len(missing_texts):
                raise ValueError("embedding provider returned an unexpected vector count")
            for position, vector in zip(missing_positions, embedded, strict=True):
                vectors[position] = vector
                if self.artifact_store:
                    self.artifact_store.put_embedding(
                        self._embedding_key(self.blocks[position].text), vector
                    )
        complete = [vector for vector in vectors if vector is not None]
        self.vectors = np.asarray(complete, dtype=np.float64) if complete else np.empty((0, 0))
        return IndexStats(
            block_count=len(blocks),
            embedded_count=len(missing_texts),
            cache_hit_count=cache_hits,
        )

    @trace_stage("retrieval.search", run_type="retriever")
    def search(
        self,
        query: str,
        *,
        document_id: str | None = None,
        k: int = 10,
    ) -> list[RetrievedBlock]:
        if k <= 0 or not self.blocks:
            return []
        assert self.bm25 is not None
        lexical = np.asarray(self.bm25.get_scores(self._tokenize(query)), dtype=np.float64)
        lexical = self._normalize_nonnegative(lexical)
        if self.embedder is None:
            semantic = np.zeros(len(self.blocks), dtype=np.float64)
        else:
            query_vector = np.asarray(self.embedder.embed([query])[0], dtype=np.float64)
            semantic = self._cosine_scores(self.vectors, query_vector)
        results: list[RetrievedBlock] = []
        for index, block in enumerate(self.blocks):
            if document_id is not None and block.document_id != document_id:
                continue
            score = self.lexical_weight * lexical[index] + self.semantic_weight * semantic[index]
            results.append(
                RetrievedBlock(
                    block=block,
                    lexical_score=float(lexical[index]),
                    semantic_score=float(semantic[index]),
                    score=float(score),
                )
            )
        results.sort(key=lambda item: (-item.score, item.block.block_id))
        return results[:k]

    def _embedding_key(self, text: str) -> str:
        if self.embedder is None:
            raise RuntimeError("embedding key requested without an embedding provider")
        model_name = getattr(self.embedder, "model_name", type(self.embedder).__name__)
        return hashlib.sha256(f"{model_name}\0{text}".encode()).hexdigest()

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return TOKEN_PATTERN.findall(text.casefold())

    @staticmethod
    def _normalize_nonnegative(values: np.ndarray) -> np.ndarray:
        values = np.maximum(values, 0)
        maximum = float(values.max()) if values.size else 0
        return values / maximum if maximum > 0 else np.zeros_like(values)

    @staticmethod
    def _cosine_scores(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
        if matrix.size == 0:
            return np.zeros(matrix.shape[0] if matrix.ndim == 2 else 0)
        matrix_norms = np.linalg.norm(matrix, axis=1)
        vector_norm = np.linalg.norm(vector)
        denominator = matrix_norms * vector_norm
        scores = np.divide(
            matrix @ vector,
            denominator,
            out=np.zeros(matrix.shape[0], dtype=np.float64),
            where=denominator != 0,
        )
        return (scores + 1) / 2
