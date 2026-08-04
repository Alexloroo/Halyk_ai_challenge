from __future__ import annotations

import logging
from dataclasses import dataclass, field

from halyk_covenants.covenants.compiler_graph import CompilerGraph
from halyk_covenants.covenants.detector import CovenantCandidate
from halyk_covenants.domain import CovenantSpec
from halyk_covenants.observability import trace_stage
from halyk_covenants.review.spec_models import SpecReviewDecision
from halyk_covenants.review.spec_reviewer import SpecReviewer

logger = logging.getLogger(__name__)


@dataclass
class SpecReviewResult:
    spec: CovenantSpec
    decision: SpecReviewDecision
    recompiled: bool = False
    recompile_decision: SpecReviewDecision | None = None


@dataclass
class SpecReviewStats:
    reviewed: int = 0
    accepted_first: int = 0
    recompiled: int = 0
    accepted_after_recompile: int = 0
    low_trust: int = 0
    skipped: list[str] = field(default_factory=list)


class SpecReviewService:
    def __init__(
        self,
        reviewer: SpecReviewer,
        compiler_graph: CompilerGraph | None = None,
    ) -> None:
        self.reviewer = reviewer
        self.compiler_graph = compiler_graph

    @trace_stage("review.spec_service", run_type="chain", tags=("review", "spec"))
    def review_and_maybe_recompile(
        self,
        spec: CovenantSpec,
        candidate: CovenantCandidate | None = None,
        document_context: str = "",
    ) -> SpecReviewResult:
        if spec.status != "compiled":
            decision = SpecReviewDecision(accepted=True, confidence=0.0, issues=["not compiled — skipped review"])
            return SpecReviewResult(spec=spec, decision=decision)

        decision = self.reviewer.review_spec(spec)

        if decision.accepted:
            updated = spec.model_copy(update={
                "spec_trust": "accepted",
                "review_confidence": decision.confidence,
            })
            return SpecReviewResult(spec=updated, decision=decision)

        if self.compiler_graph is None or candidate is None:
            updated = spec.model_copy(update={
                "spec_trust": "low",
                "review_objection": decision.objection,
                "review_confidence": decision.confidence,
            })
            return SpecReviewResult(spec=updated, decision=decision)

        recompiled_spec = self._bounded_recompile(
            spec, candidate, document_context, decision.objection or "spec rejected"
        )

        if recompiled_spec is None:
            updated = spec.model_copy(update={
                "spec_trust": "low",
                "review_objection": decision.objection,
                "review_confidence": decision.confidence,
            })
            return SpecReviewResult(spec=updated, decision=decision)

        recompile_decision = self.reviewer.review_spec(recompiled_spec)

        if recompile_decision.accepted:
            updated = recompiled_spec.model_copy(update={
                "spec_trust": "revised",
                "review_objection": decision.objection,
                "review_confidence": recompile_decision.confidence,
            })
            return SpecReviewResult(
                spec=updated,
                decision=decision,
                recompiled=True,
                recompile_decision=recompile_decision,
            )

        updated = recompiled_spec.model_copy(update={
            "spec_trust": "low",
            "review_objection": recompile_decision.objection or decision.objection,
            "review_confidence": recompile_decision.confidence,
        })
        return SpecReviewResult(
            spec=updated,
            decision=decision,
            recompiled=True,
            recompile_decision=recompile_decision,
        )

    @trace_stage("review.spec_recompile", run_type="chain", tags=("review", "recompile"))
    def _bounded_recompile(
        self,
        original_spec: CovenantSpec,
        candidate: CovenantCandidate,
        document_context: str,
        objection: str,
    ) -> CovenantSpec | None:
        augmented_context = (
            f"{document_context}\n\n"
            f"REVIEWER OBJECTION (address this specific issue):\n{objection}\n"
            f"The previous attempt was rejected for the reason above. "
            f"Fix exactly this discrepancy. The covenant text has not changed."
        )

        final = self.compiler_graph.invoke(
            {"candidate": candidate, "context": augmented_context, "attempt": 0}
        )

        if final.get("status") == "compiled" and final["outcome"].specs:
            return final["outcome"].specs[0]

        logger.warning(
            "Bounded recompile failed for %s: %s",
            candidate.candidate_id,
            final.get("validation_errors", []),
        )
        return None

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
            candidate = candidates.get(spec.covenant_id)
            context = contexts.get(spec.covenant_id, "")

            result = self.review_and_maybe_recompile(spec, candidate, context)
            results.append(result)

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
            "Spec review batch: %d reviewed, %d accepted, %d recompiled (%d accepted), %d low trust",
            stats.reviewed, stats.accepted_first, stats.recompiled,
            stats.accepted_after_recompile, stats.low_trust,
        )
        return results, stats
