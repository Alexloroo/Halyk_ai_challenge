from __future__ import annotations

import logging
from typing import Any

from halyk_covenants.documents.retrieval import HybridRetriever
from halyk_covenants.observability import trace_stage

logger = logging.getLogger(__name__)


class RetrieverContextExpander:
    """Re-runs retrieval with a targeted query and returns only genuinely new blocks.

    Used once per covenant, and only when the context grader reports that the
    original context could not have contained the answer.
    """

    def __init__(self, retriever: HybridRetriever, *, k: int = 8) -> None:
        if k <= 0:
            raise ValueError("k must be positive")
        self.retriever = retriever
        self.k = k

    @trace_stage("review.expand.search", run_type="retriever", tags=("review", "rag"))
    def expand(self, query: str, candidate: Any, current_context: str) -> str:
        hits = self.retriever.search(query, k=self.k)
        if not hits:
            return ""

        allowed = set(getattr(candidate, "borrower_ids", []) or [])
        lines: list[str] = []
        for hit in hits:
            block = hit.block
            text = block.text.strip()
            if not text:
                continue
            # A block scoped to other borrowers cannot explain this covenant.
            if allowed and block.borrower_ids and not allowed.intersection(block.borrower_ids):
                continue
            # Skip what the compiler already saw — expansion must add, not repeat.
            if text[:120] in current_context:
                continue
            lines.append(
                f"[document={block.document_id} page={block.page} "
                f"type={block.block_type}] {text}"
            )

        if not lines:
            logger.info("Retrieval expansion for %r added no new blocks", query[:80])
        return "\n".join(lines)
