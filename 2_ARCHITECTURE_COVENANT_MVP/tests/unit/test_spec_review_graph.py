"""The RAG loop: grade the retrieved context, expand it once, then recompile.

Covers the case that plain recompilation can never fix — the context never
contained the answer, so recompiling with the same context is futile.
"""

from __future__ import annotations

from decimal import Decimal

from halyk_covenants.domain import ConditionSpec, CovenantSpec, MetricSpec, SourceRef
from halyk_covenants.review.spec_models import ContextGrade, SpecReviewDecision
from halyk_covenants.review.spec_review_graph import (
    MAX_EXPANSIONS,
    MAX_RECOMPILES,
    MAX_REVIEWS,
    SpecReviewGraph,
)


def build_spec(covenant_id: str = "COV-BETA-MAX", **overrides) -> CovenantSpec:
    payload = {
        "covenant_id": covenant_id,
        "raw_text": "MAX одной операции ≤ 7 000 000",  # currency cell is empty
        "borrower_ids": ["B002"],
        "metric": MetricSpec(metric_type="max", field="amount"),
        "condition": ConditionSpec(comparator="<=", threshold=Decimal("7000000")),
        "source": SourceRef(document_id="borrower_limits_appendix.pdf", page=1),
        "confidence": 0.8,
    }
    payload.update(overrides)
    return CovenantSpec(**payload)


class Reviewer:
    model_name = "stub"
    prompt_version = "stub-v2"

    def __init__(self, decisions, grades=None) -> None:
        self.decisions = list(decisions)
        self.grades = list(grades or [])
        self.contexts_seen: list[str] = []
        self.grade_calls = 0

    def review_spec(self, spec, context: str = "") -> SpecReviewDecision:
        self.contexts_seen.append(context)
        return (
            self.decisions.pop(0)
            if self.decisions
            else SpecReviewDecision(accepted=True, confidence=1.0)
        )

    def grade_context(self, spec, context: str, objection: str) -> ContextGrade:
        self.grade_calls += 1
        return self.grades.pop(0) if self.grades else ContextGrade(sufficient=True, confidence=0.9)


class Compiler:
    def __init__(self, spec: CovenantSpec | None, status: str = "compiled") -> None:
        self.spec = spec
        self.status = status
        self.contexts: list[str] = []

    def invoke(self, state):  # type: ignore[no-untyped-def]
        self.contexts.append(state.get("context", ""))

        class Outcome:
            specs = [self.spec] if self.spec is not None else []

        return {"status": self.status, "outcome": Outcome(), "validation_errors": []}


class Expander:
    def __init__(self, extra: str = "") -> None:
        self.extra = extra
        self.queries: list[str] = []

    def expand(self, query: str, candidate, current_context: str) -> str:
        self.queries.append(query)
        return self.extra


CANDIDATE = object()  # opaque to the graph; only forwarded to the compiler


def run(graph: SpecReviewGraph, spec: CovenantSpec, candidate=CANDIDATE):
    return graph.invoke(
        {
            "spec": spec,
            "original_spec": spec,
            "candidate": candidate,
            "context": "TABLE ROW: MAX одной операции | 7 000 000 | <empty currency>",
            "reviews": 0,
            "expansions": 0,
            "recompiles": 0,
        }
    )


# --- accepted on the first pass ---------------------------------------------------


def test_accepted_spec_never_reaches_the_grader():
    reviewer = Reviewer([SpecReviewDecision(accepted=True, confidence=0.94)])
    compiler = Compiler(spec=None)
    expander = Expander()
    graph = SpecReviewGraph(reviewer=reviewer, compiler_graph=compiler, expander=expander)

    final = run(graph, build_spec())

    assert final["spec"].spec_trust == "accepted"
    assert reviewer.grade_calls == 0
    assert compiler.contexts == []
    assert expander.queries == []


def test_reviewer_receives_the_document_context():
    """Without context the reviewer cannot tell a misread from a missing footnote."""
    reviewer = Reviewer([SpecReviewDecision(accepted=True, confidence=0.9)])
    graph = SpecReviewGraph(reviewer=reviewer)

    run(graph, build_spec())

    assert "empty currency" in reviewer.contexts_seen[0]


# --- cause A: context was fine, the compiler misread it ---------------------------


def test_sufficient_context_recompiles_without_expanding():
    revised = build_spec(
        condition=ConditionSpec(comparator="<=", threshold=Decimal("7000000"), currency="KZT")
    )
    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.3, objection="currency is missing"),
            SpecReviewDecision(accepted=True, confidence=0.9),
        ],
        grades=[ContextGrade(sufficient=True, confidence=0.85)],
    )
    compiler = Compiler(spec=revised)
    expander = Expander(extra="SHOULD NOT BE USED")
    graph = SpecReviewGraph(reviewer=reviewer, compiler_graph=compiler, expander=expander)

    final = run(graph, build_spec())

    assert reviewer.grade_calls == 1
    assert expander.queries == [], "sufficient context must not trigger a re-search"
    assert len(compiler.contexts) == 1
    assert final["spec"].spec_trust == "revised"


# --- cause B: the context never contained the answer ------------------------------


def test_insufficient_context_triggers_one_targeted_re_search():
    revised = build_spec(
        condition=ConditionSpec(comparator="<=", threshold=Decimal("7000000"), currency="KZT")
    )
    footnote = "* Пустая валюта в строке MAX означает KZT согласно вводному определению"
    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.25, objection="currency unknown"),
            SpecReviewDecision(accepted=True, confidence=0.91),
        ],
        grades=[
            ContextGrade(
                sufficient=False,
                missing_query="пустая валюта означает",
                confidence=0.8,
                reasoning="the footnote defining the empty cell was not retrieved",
            )
        ],
    )
    compiler = Compiler(spec=revised)
    expander = Expander(extra=footnote)
    graph = SpecReviewGraph(reviewer=reviewer, compiler_graph=compiler, expander=expander)

    final = run(graph, build_spec())

    assert expander.queries == ["пустая валюта означает"], "grader's query drives the re-search"
    assert footnote in compiler.contexts[0], "recompile must see the newly retrieved text"
    assert "EXPANDED_RETRIEVAL" in compiler.contexts[0]
    assert final["expanded_context"] is True
    assert final["spec"].spec_trust == "revised"
    assert final["spec"].condition.currency == "KZT"


def test_expansion_falls_back_to_clause_text_when_grader_gives_no_query():
    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.2, objection="unclear"),
            SpecReviewDecision(accepted=True, confidence=0.8),
        ],
        grades=[ContextGrade(sufficient=False, missing_query=None, confidence=0.5)],
    )
    compiler = Compiler(spec=build_spec())
    expander = Expander(extra="something")
    graph = SpecReviewGraph(reviewer=reviewer, compiler_graph=compiler, expander=expander)

    run(graph, build_spec())

    assert expander.queries == ["MAX одной операции ≤ 7 000 000"]


# --- hard bounds ------------------------------------------------------------------


def test_loop_terminates_when_every_pass_rejects():
    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.1, objection="no") for _ in range(8)
        ],
        grades=[
            ContextGrade(sufficient=False, missing_query="q", confidence=0.5) for _ in range(8)
        ],
    )
    compiler = Compiler(spec=build_spec())
    expander = Expander(extra="more")
    graph = SpecReviewGraph(reviewer=reviewer, compiler_graph=compiler, expander=expander)

    final = run(graph, build_spec())

    assert final["reviews"] == MAX_REVIEWS
    assert final["expansions"] == MAX_EXPANSIONS
    assert final["recompiles"] == MAX_RECOMPILES
    assert reviewer.grade_calls == 1
    assert final["spec"].spec_trust == "low"


def test_bounds_are_the_documented_values():
    assert (MAX_REVIEWS, MAX_EXPANSIONS, MAX_RECOMPILES) == (2, 1, 1)


# --- degradation ------------------------------------------------------------------


def test_grader_failure_is_treated_as_sufficient_context():
    class ExplodingGrader(Reviewer):
        def grade_context(self, spec, context, objection):
            self.grade_calls += 1
            raise RuntimeError("grader unavailable")

    reviewer = ExplodingGrader(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.3, objection="x"),
            SpecReviewDecision(accepted=True, confidence=0.9),
        ]
    )
    compiler = Compiler(spec=build_spec())
    graph = SpecReviewGraph(reviewer=reviewer, compiler_graph=compiler, expander=Expander("e"))

    final = run(graph, build_spec())

    assert len(compiler.contexts) == 1, "recompile still happens"
    assert final["spec"].spec_trust == "revised"


def test_expander_failure_still_recompiles_with_the_original_context():
    class ExplodingExpander:
        def expand(self, query, candidate, current_context):
            raise RuntimeError("retriever down")

    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.3, objection="x"),
            SpecReviewDecision(accepted=True, confidence=0.9),
        ],
        grades=[ContextGrade(sufficient=False, missing_query="q", confidence=0.6)],
    )
    compiler = Compiler(spec=build_spec())
    graph = SpecReviewGraph(
        reviewer=reviewer, compiler_graph=compiler, expander=ExplodingExpander()
    )

    final = run(graph, build_spec())

    assert len(compiler.contexts) == 1
    assert "EXPANDED_RETRIEVAL" not in compiler.contexts[0]
    assert final["expanded_context"] is False


def test_without_an_expander_the_graph_skips_straight_to_recompile():
    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.3, objection="x"),
            SpecReviewDecision(accepted=True, confidence=0.9),
        ],
        grades=[ContextGrade(sufficient=False, missing_query="q", confidence=0.6)],
    )
    compiler = Compiler(spec=build_spec())
    graph = SpecReviewGraph(reviewer=reviewer, compiler_graph=compiler, expander=None)

    final = run(graph, build_spec())

    assert final.get("expansions", 0) == 0
    assert len(compiler.contexts) == 1


def test_failed_recompile_ends_the_loop_without_a_second_review():
    reviewer = Reviewer(
        decisions=[SpecReviewDecision(accepted=False, confidence=0.3, objection="x")],
        grades=[ContextGrade(sufficient=True, confidence=0.7)],
    )
    compiler = Compiler(spec=None, status="failed_compilation")
    graph = SpecReviewGraph(reviewer=reviewer, compiler_graph=compiler, expander=Expander())

    final = run(graph, build_spec())

    assert final["reviews"] == 1, "no second review after a failed recompile"
    assert final["spec"].spec_trust == "low"
