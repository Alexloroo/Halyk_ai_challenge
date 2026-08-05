from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.observability import trace_stage
from halyk_covenants.review.spec_models import ContextGrade, SpecReviewDecision
from halyk_covenants.review.spec_review_graph import SpecReviewGraph
from halyk_covenants.review.spec_reviewer import SpecReviewer

if TYPE_CHECKING:
    from halyk_covenants.covenants.compiler_graph import CompilerGraph
    from halyk_covenants.covenants.detector import CovenantCandidate
    from halyk_covenants.review.spec_review_graph import ContextExpander

logger = logging.getLogger(__name__)


@dataclass
class SpecReviewResult:
    spec: CovenantSpec
    decision: SpecReviewDecision
    recompiled: bool = False
    recompile_decision: SpecReviewDecision | None = None
    context_grade: ContextGrade | None = None
    context_expanded: bool = False


@dataclass
class SpecReviewStats:
    reviewed: int = 0
    accepted_first: int = 0
    recompiled: int = 0
    accepted_after_recompile: int = 0
    context_expanded: int = 0
    low_trust: int = 0
    skipped: list[str] = field(default_factory=list)


class SpecReviewService:
    """Public entrypoint for spec review. Drives :class:`SpecReviewGraph`."""

    def __init__(
        self,
        reviewer: SpecReviewer,
        compiler_graph: CompilerGraph | None = None,
        expander: ContextExpander | None = None,
    ) -> None:
        self.reviewer = reviewer
        self.compiler_graph = compiler_graph
        self.expander = expander
        self.graph = SpecReviewGraph(
            reviewer=reviewer,
            compiler_graph=compiler_graph,
            expander=expander,
        )

    def attach_expander(self, expander: ContextExpander) -> None:
        """Wire in retrieval expansion once the document index exists.

        The retriever is only built after documents are ingested, which happens
        after this service is constructed.
        """
        self.expander = expander
        self.graph = SpecReviewGraph(
            reviewer=self.reviewer,
            compiler_graph=self.compiler_graph,
            expander=expander,
        )

    @trace_stage("review.spec_service", run_type="chain", tags=("review", "spec"))
    def review_and_maybe_recompile(
        self,
        spec: CovenantSpec,
        candidate: CovenantCandidate | None = None,
        document_context: str = "",
    ) -> SpecReviewResult:
        if spec.status != "compiled":
            decision = SpecReviewDecision(
                accepted=True,
                confidence=0.0,
                issues=["not compiled — skipped review"],
            )
            return SpecReviewResult(spec=spec, decision=decision)

        final = self.graph.invoke(
            {
                "spec": spec,
                "original_spec": spec,
                "candidate": candidate,
                "context": document_context,
                "reviews": 0,
                "expansions": 0,
                "recompiles": 0,
            }
        )

        return SpecReviewResult(
            spec=final["spec"],
            decision=final["decision"],
            recompiled=final.get("recompiles", 0) > 0,
            recompile_decision=final["decision"] if final.get("recompiles", 0) else None,
            context_grade=final.get("grade"),
            context_expanded=final.get("expanded_context", False),
        )

    @trace_stage("review.spec_batch", run_type="chain", tags=("review", "spec", "batch"))
    def review_batch(
        self,
        specs: list[CovenantSpec],
        candidates: dict[str, CovenantCandidate] | None = None,
        contexts: dict[str, str] | None = None,
    ) -> tuple[list[SpecReviewResult], SpecReviewStats]:
        candidates = candidates or {}
        contexts = contexts or {}
        stats = SpecReviewStats()
        results: list[SpecReviewResult] = []

        for spec in specs:
            stats.reviewed += 1
            result = self.review_and_maybe_recompile(
                spec,
                candidates.get(spec.covenant_id),
                contexts.get(spec.covenant_id, ""),
            )
            results.append(result)

            if result.context_expanded:
                stats.context_expanded += 1
            if result.spec.spec_trust == "accepted":
                stats.accepted_first += 1
            elif result.recompiled:
                stats.recompiled += 1
                if result.spec.spec_trust == "revised":
                    stats.accepted_after_recompile += 1
                else:
                    stats.low_trust += 1
            else:
                stats.low_trust += 1

        logger.info(
            "Spec review batch: %d reviewed, %d accepted, %d recompiled "
            "(%d accepted), %d context expansions, %d low trust",
            stats.reviewed,
            stats.accepted_first,
            stats.recompiled,
            stats.accepted_after_recompile,
            stats.context_expanded,
            stats.low_trust,
        )
        return results, stats
