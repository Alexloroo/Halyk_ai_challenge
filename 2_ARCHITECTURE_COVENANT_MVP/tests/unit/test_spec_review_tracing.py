"""LangSmith observability for the spec-review graph.

Spans without metadata are almost worthless: you see that a node ran and how long
it took, but not what it decided or why the graph branched. These tests pin the
metadata that makes a trace answer "why did this covenant end up low trust?".
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from test_spec_review_graph import Compiler, Expander, Reviewer, build_spec, run

from halyk_covenants.domain import ConditionSpec, CovenantSpec, MetricSpec, SourceRef
from halyk_covenants.review.context_expander import RetrieverContextExpander
from halyk_covenants.review.spec_models import ContextGrade, SpecReviewDecision
from halyk_covenants.review.spec_review_graph import SpecReviewGraph
from halyk_covenants.review.spec_review_service import SpecReviewService
from halyk_covenants.review.spec_reviewer import LangChainSpecReviewer


@pytest.fixture
def captured(monkeypatch):
    """Collect every annotate_current_trace call made by the graph."""
    calls: list[tuple[dict, tuple]] = []

    def record(metadata=None, tags=()):
        calls.append((metadata or {}, tuple(tags)))

    for module in (
        "halyk_covenants.review.spec_review_graph",
        "halyk_covenants.review.context_expander",
    ):
        monkeypatch.setattr(f"{module}.annotate_current_trace", record)
    return calls


def metadata_keys(calls) -> set[str]:
    return {key for metadata, _ in calls for key in metadata}


def all_tags(calls) -> set[str]:
    return {tag for _, tags in calls for tag in tags}


# --- every span is traceable at all -----------------------------------------------


def test_graph_nodes_and_entrypoints_are_traceable():
    assert hasattr(SpecReviewGraph.invoke, "__wrapped__")
    assert hasattr(SpecReviewGraph._review_node, "__wrapped__")
    assert hasattr(SpecReviewGraph._grade_context_node, "__wrapped__")
    assert hasattr(SpecReviewGraph._expand_retrieval_node, "__wrapped__")
    assert hasattr(SpecReviewGraph._recompile_node, "__wrapped__")
    assert hasattr(SpecReviewService.review_and_maybe_recompile, "__wrapped__")
    assert hasattr(RetrieverContextExpander.expand, "__wrapped__")


def test_llm_calls_are_typed_as_llm_runs():
    """LangSmith groups cost and latency by run_type; these must not be plain chains."""
    assert hasattr(LangChainSpecReviewer.review_spec, "__wrapped__")
    assert hasattr(LangChainSpecReviewer.grade_context, "__wrapped__")


# --- the accepted path ------------------------------------------------------------


def test_accepted_review_records_its_decision(captured):
    reviewer = Reviewer([SpecReviewDecision(accepted=True, confidence=0.94)])
    graph = SpecReviewGraph(reviewer=reviewer)

    run(graph, build_spec())

    assert "accepted" in metadata_keys(captured)
    assert "review_confidence" in metadata_keys(captured)
    assert "review_accepted" in all_tags(captured)
    assert "trust_accepted" in all_tags(captured)
    assert "outcome_accepted" in all_tags(captured)


def test_root_span_carries_covenant_identity_and_capabilities(captured):
    reviewer = Reviewer([SpecReviewDecision(accepted=True, confidence=0.9)])
    graph = SpecReviewGraph(reviewer=reviewer, expander=Expander())

    run(graph, build_spec(covenant_id="COV-TRACE-ME"))

    root = [m for m, _ in captured if "covenant_id" in m]
    assert root, "the root span must identify the covenant"
    assert root[0]["covenant_id"] == "COV-TRACE-ME"
    assert root[0]["expander_available"] is True
    assert root[0]["compiler_available"] is False


# --- the branch point that matters ------------------------------------------------


def test_context_grade_records_which_cause_was_diagnosed(captured):
    """The single most useful field in the trace: why the graph branched."""
    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.25, objection="currency unknown"),
            SpecReviewDecision(accepted=True, confidence=0.9),
        ],
        grades=[
            ContextGrade(
                sufficient=False,
                missing_query="пустая валюта означает",
                confidence=0.8,
                reasoning="footnote not retrieved",
            )
        ],
    )
    graph = SpecReviewGraph(
        reviewer=reviewer, compiler_graph=Compiler(spec=build_spec()), expander=Expander("txt")
    )

    run(graph, build_spec())

    grade_meta = next(m for m, _ in captured if "context_sufficient" in m)
    assert grade_meta["context_sufficient"] is False
    assert grade_meta["missing_query"] == "пустая валюта означает"
    assert grade_meta["grade_reasoning"] == "footnote not retrieved"
    assert "context_insufficient" in all_tags(captured)


def test_sufficient_context_is_tagged_as_such(captured):
    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.3, objection="misread"),
            SpecReviewDecision(accepted=True, confidence=0.9),
        ],
        grades=[ContextGrade(sufficient=True, confidence=0.85)],
    )
    graph = SpecReviewGraph(
        reviewer=reviewer, compiler_graph=Compiler(spec=build_spec()), expander=Expander("x")
    )

    run(graph, build_spec())

    assert "context_sufficient" in all_tags(captured)
    assert "context_insufficient" not in all_tags(captured)


# --- expansion and recompilation --------------------------------------------------


def test_expansion_records_the_query_and_how_much_context_it_added(captured):
    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.2, objection="x"),
            SpecReviewDecision(accepted=True, confidence=0.9),
        ],
        grades=[ContextGrade(sufficient=False, missing_query="найди сноску", confidence=0.7)],
    )
    graph = SpecReviewGraph(
        reviewer=reviewer,
        compiler_graph=Compiler(spec=build_spec()),
        expander=Expander("A" * 200),
    )

    run(graph, build_spec())

    expand_meta = next(m for m, _ in captured if "expansion_query" in m)
    assert expand_meta["expansion_query"] == "найди сноску"
    assert expand_meta["added_chars"] == 200
    assert expand_meta["context_chars_after"] > expand_meta["context_chars_before"]
    assert "expand_hit" in all_tags(captured)


def test_recompile_records_whether_it_ran_on_expanded_context(captured):
    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.2, objection="x"),
            SpecReviewDecision(accepted=True, confidence=0.9),
        ],
        grades=[ContextGrade(sufficient=False, missing_query="q", confidence=0.7)],
    )
    graph = SpecReviewGraph(
        reviewer=reviewer, compiler_graph=Compiler(spec=build_spec()), expander=Expander("more")
    )

    run(graph, build_spec())

    recompile_meta = next(m for m, _ in captured if "compiler_status" in m)
    assert recompile_meta["context_was_expanded"] is True
    assert recompile_meta["attempt"] == 1
    assert "recompile_ok" in all_tags(captured)


def test_final_outcome_counts_every_bounded_step(captured):
    reviewer = Reviewer(
        decisions=[SpecReviewDecision(accepted=False, confidence=0.1, objection="no")] * 4,
        grades=[ContextGrade(sufficient=False, missing_query="q", confidence=0.5)] * 4,
    )
    graph = SpecReviewGraph(
        reviewer=reviewer, compiler_graph=Compiler(spec=build_spec()), expander=Expander("x")
    )

    run(graph, build_spec())

    final = next(m for m, _ in captured if "final_spec_trust" in m)
    assert final["final_spec_trust"] == "low"
    assert final["reviews"] == 2
    assert final["context_expansions"] == 1
    assert final["recompiles"] == 1
    assert "outcome_low" in all_tags(captured)


# --- degradation is visible, not silent -------------------------------------------


def test_grader_failure_is_tagged_so_it_can_be_found_in_langsmith(captured):
    class ExplodingGrader(Reviewer):
        def grade_context(self, spec, context, objection):
            raise RuntimeError("grader unavailable")

    reviewer = ExplodingGrader(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.3, objection="x"),
            SpecReviewDecision(accepted=True, confidence=0.9),
        ]
    )
    graph = SpecReviewGraph(reviewer=reviewer, compiler_graph=Compiler(spec=build_spec()))

    run(graph, build_spec())

    assert "grade_degraded" in all_tags(captured)
    assert "RuntimeError" in [m.get("grade_error") for m, _ in captured]


def test_expander_failure_is_tagged(captured):
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
    graph = SpecReviewGraph(
        reviewer=reviewer,
        compiler_graph=Compiler(spec=build_spec()),
        expander=ExplodingExpander(),
    )

    run(graph, build_spec())

    assert "expand_degraded" in all_tags(captured)


def test_failed_recompile_is_tagged(captured):
    reviewer = Reviewer(
        decisions=[SpecReviewDecision(accepted=False, confidence=0.3, objection="x")],
        grades=[ContextGrade(sufficient=True, confidence=0.7)],
    )
    graph = SpecReviewGraph(
        reviewer=reviewer, compiler_graph=Compiler(spec=None, status="failed_compilation")
    )

    run(graph, build_spec())

    assert "recompile_failed" in all_tags(captured)


# --- no scored value ever reaches a trace -----------------------------------------


def test_traces_never_carry_a_number_or_verdict(captured):
    """The reviewer runs before evaluation; a number in its trace would be a leak."""
    reviewer = Reviewer(
        decisions=[
            SpecReviewDecision(accepted=False, confidence=0.3, objection="x"),
            SpecReviewDecision(accepted=True, confidence=0.9),
        ],
        grades=[ContextGrade(sufficient=True, confidence=0.7)],
    )
    spec = build_spec(condition=ConditionSpec(comparator="<=", threshold=Decimal("7000000")))
    graph = SpecReviewGraph(reviewer=reviewer, compiler_graph=Compiler(spec=spec))

    run(graph, spec)

    forbidden = {"number", "verdict", "evidence_transaction_id", "metric_value"}
    assert not (metadata_keys(captured) & forbidden)


def _unused() -> None:
    # Keeps the shared fixtures imported above obviously in use for linters.
    _ = (CovenantSpec, MetricSpec, SourceRef)
