"""LangGraph loop that reviews a compiled specification before evaluation.

The loop exists because a rejected specification has two distinct causes, and only
one of them is fixable by recompiling:

    review ──accepted──────────────────────────────────────────────► END
       │
       └─rejected─► grade_context ──sufficient──────────► recompile ─┐
                          │                                          │
                          └─insufficient─► expand_retrieval ─────────┘
                                           (one bounded re-search)   │
                                                                     │
                          ┌──────────────────────────────────────────┘
                          ▼
                       review  (second pass, then forced to END)

Hard bounds, all structural rather than heuristic:
    reviews          <= 2
    context grades   <= 1
    retrieval expand <= 1
    recompiles       <= 1

The reviewer never sees transaction data, numbers, verdicts or evidence — at this
point in the pipeline none of them exist yet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.observability import trace_stage
from halyk_covenants.review.spec_models import ContextGrade, SpecReviewDecision
from halyk_covenants.review.spec_reviewer import SpecReviewer

if TYPE_CHECKING:
    from halyk_covenants.covenants.compiler_graph import CompilerGraph

logger = logging.getLogger(__name__)

MAX_REVIEWS = 2
MAX_EXPANSIONS = 1
MAX_RECOMPILES = 1


class ContextExpander(Protocol):
    """Retrieves additional document context for a targeted query."""

    def expand(self, query: str, candidate: Any, current_context: str) -> str: ...


class SpecReviewState(TypedDict, total=False):
    spec: CovenantSpec
    original_spec: CovenantSpec
    # Opaque to this graph — only forwarded to the compiler. Typed as Any because
    # TypedDict annotations are resolved at runtime and CovenantCandidate cannot be
    # imported here without reintroducing a circular import.
    candidate: Any
    context: str
    decision: SpecReviewDecision
    grade: ContextGrade | None
    reviews: int
    expansions: int
    recompiles: int
    expanded_context: bool
    status: str


class SpecReviewGraph:
    def __init__(
        self,
        *,
        reviewer: SpecReviewer,
        compiler_graph: CompilerGraph | None = None,
        expander: ContextExpander | None = None,
    ) -> None:
        self.reviewer = reviewer
        self.compiler_graph = compiler_graph
        self.expander = expander
        self.graph = self._build_graph()

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(SpecReviewState)
        builder.add_node("review", self._review_node)
        builder.add_node("grade_context", self._grade_context_node)
        builder.add_node("expand_retrieval", self._expand_retrieval_node)
        builder.add_node("recompile", self._recompile_node)

        builder.add_edge(START, "review")
        builder.add_conditional_edges(
            "review",
            self._route_after_review,
            {"done": END, "grade": "grade_context"},
        )
        builder.add_conditional_edges(
            "grade_context",
            self._route_after_grade,
            {"expand": "expand_retrieval", "recompile": "recompile", "done": END},
        )
        builder.add_edge("expand_retrieval", "recompile")
        # Cycles back to review; the counters below make the cycle terminate.
        builder.add_conditional_edges(
            "recompile",
            self._route_after_recompile,
            {"review": "review", "done": END},
        )
        return builder.compile()

    # --- nodes --------------------------------------------------------------------

    @trace_stage("review.graph.review", run_type="chain", tags=("review", "spec"))
    def _review_node(self, state: SpecReviewState) -> SpecReviewState:
        spec = state["spec"]
        decision = self.reviewer.review_spec(spec, state.get("context", ""))
        reviews = state.get("reviews", 0) + 1

        if decision.accepted:
            trust = "revised" if state.get("recompiles", 0) else "accepted"
            spec = spec.model_copy(
                update={"spec_trust": trust, "review_confidence": decision.confidence}
            )
            return {"spec": spec, "decision": decision, "reviews": reviews, "status": "accepted"}

        spec = spec.model_copy(
            update={
                "spec_trust": "low",
                "review_objection": decision.objection,
                "review_confidence": decision.confidence,
            }
        )
        return {"spec": spec, "decision": decision, "reviews": reviews, "status": "rejected"}

    @trace_stage("review.graph.grade_context", run_type="chain", tags=("review", "rag"))
    def _grade_context_node(self, state: SpecReviewState) -> SpecReviewState:
        decision = state["decision"]
        try:
            grade = self.reviewer.grade_context(
                state["spec"], state.get("context", ""), decision.objection or ""
            )
        except Exception as exc:
            # A failed grade must not sink the covenant: assume the context was fine
            # and let the ordinary recompile attempt proceed.
            logger.warning("Context grading failed, assuming sufficient: %s", exc)
            grade = ContextGrade(sufficient=True, confidence=0.0, reasoning=str(exc)[:200])
        return {"grade": grade}

    @trace_stage("review.graph.expand_retrieval", run_type="retriever", tags=("review", "rag"))
    def _expand_retrieval_node(self, state: SpecReviewState) -> SpecReviewState:
        grade = state.get("grade")
        query = (grade.missing_query if grade else None) or state["spec"].raw_text
        try:
            extra = self.expander.expand(query, state.get("candidate"), state.get("context", ""))
        except Exception as exc:
            logger.warning("Retrieval expansion failed: %s", exc)
            extra = ""

        context = state.get("context", "")
        if extra:
            context = f"{context}\n\nEXPANDED_RETRIEVAL (query: {query}):\n{extra}"
        return {
            "context": context,
            "expansions": state.get("expansions", 0) + 1,
            "expanded_context": bool(extra),
        }

    @trace_stage("review.graph.recompile", run_type="chain", tags=("review", "recompile"))
    def _recompile_node(self, state: SpecReviewState) -> SpecReviewState:
        decision = state["decision"]
        objection = decision.objection or "specification rejected"
        augmented = (
            f"{state.get('context', '')}\n\n"
            f"REVIEWER OBJECTION (address this specific issue):\n{objection}\n"
            f"The previous attempt was rejected for the reason above. "
            f"Fix exactly this discrepancy. The covenant text has not changed."
        )
        recompiles = state.get("recompiles", 0) + 1

        final = self.compiler_graph.invoke(
            {"candidate": state["candidate"], "context": augmented, "attempt": 0}
        )
        if final.get("status") == "compiled" and final["outcome"].specs:
            return {"spec": final["outcome"].specs[0], "recompiles": recompiles}

        logger.warning(
            "Bounded recompile failed for %s: %s",
            getattr(state.get("candidate"), "candidate_id", "<unknown candidate>"),
            final.get("validation_errors", []),
        )
        # Keep the original specification, already marked low trust by the review node.
        return {"recompiles": recompiles, "status": "recompile_failed"}

    # --- routing ------------------------------------------------------------------

    def _route_after_review(self, state: SpecReviewState) -> str:
        if state.get("status") == "accepted":
            return "done"
        if state.get("reviews", 0) >= MAX_REVIEWS:
            return "done"
        if self.compiler_graph is None or state.get("candidate") is None:
            return "done"
        return "grade"

    def _route_after_grade(self, state: SpecReviewState) -> str:
        grade = state.get("grade")
        needs_more_context = grade is not None and not grade.sufficient
        can_expand = (
            self.expander is not None and state.get("expansions", 0) < MAX_EXPANSIONS
        )
        if needs_more_context and can_expand:
            return "expand"
        if state.get("recompiles", 0) < MAX_RECOMPILES:
            return "recompile"
        return "done"

    def _route_after_recompile(self, state: SpecReviewState) -> str:
        if state.get("status") == "recompile_failed":
            return "done"
        return "review" if state.get("reviews", 0) < MAX_REVIEWS else "done"

    # --- entrypoint ---------------------------------------------------------------

    @trace_stage("review.graph", run_type="chain", tags=("review", "spec", "langgraph"))
    def invoke(self, initial: SpecReviewState) -> SpecReviewState:
        return self.graph.invoke(
            initial,
            # reviews + grade + expand + recompile + margin
            config={"recursion_limit": 12},
        )
