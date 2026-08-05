from __future__ import annotations

import logging
from typing import Any

from halyk_covenants.documents.retrieval import HybridRetriever
from halyk_covenants.observability import annotate_current_trace, trace_stage

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
            annotate_current_trace(
                metadata={"query": query, "hits": 0, "kept": 0}, tags=("expand_no_hits",)
            )
            return ""

        allowed = set(getattr(candidate, "borrower_ids", []) or [])
        lines: list[str] = []
        dropped_scope = dropped_duplicate = 0
        for hit in hits:
            block = hit.block
            text = block.text.strip()
            if not text:
                continue
            # A block scoped to other borrowers cannot explain this covenant.
            if allowed and block.borrower_ids and not allowed.intersection(block.borrower_ids):
                dropped_scope += 1
                continue
            # Skip what the compiler already saw — expansion must add, not repeat.
            if text[:120] in current_context:
                dropped_duplicate += 1
                continue
            lines.append(
                f"[document={block.document_id} page={block.page} "
                f"type={block.block_type}] {text}"
            )

        annotate_current_trace(
            metadata={
                "query": query,
                "hits": len(hits),
                "kept": len(lines),
                "dropped_out_of_scope": dropped_scope,
                "dropped_already_seen": dropped_duplicate,
                "top_score": hits[0].score if hits else None,
            },
            tags=("expand_kept" if lines else "expand_all_filtered",),
        )
        if not lines:
            logger.info("Retrieval expansion for %r added no new blocks", query[:80])
        return "\n".join(lines)
