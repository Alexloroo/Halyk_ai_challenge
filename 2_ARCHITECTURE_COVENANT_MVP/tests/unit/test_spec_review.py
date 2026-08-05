"""Cloud1 spec review: the reviewer runs before evaluation and cannot touch numbers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from halyk_covenants.domain import (
    ConditionSpec,
    CovenantSpec,
    MetricSpec,
    SourceRef,
)
from halyk_covenants.review.spec_models import SpecReviewDecision
from halyk_covenants.review.spec_review_service import SpecReviewService


def build_spec(covenant_id: str = "COV-1", **overrides) -> CovenantSpec:
    payload = {
        "covenant_id": covenant_id,
        "raw_text": "Monthly outgoing KZT payments must not exceed 50,000,000 KZT.",
        "borrower_ids": ["B001"],
        "metric": MetricSpec(metric_type="sum", field="amount"),
        "condition": ConditionSpec(comparator="<=", threshold=Decimal("50000000")),
        "source": SourceRef(document_id="doc-1", page=1),
        "confidence": 0.9,
        "effective_from": date(2026, 1, 1),
    }
    payload.update(overrides)
    return CovenantSpec(**payload)


class StubReviewer:
    """Deterministic reviewer. Returns queued decisions in order."""

    model_name = "stub"
    prompt_version = "stub-v1"

    def __init__(self, *decisions: SpecReviewDecision) -> None:
        self.queue = list(decisions)
        self.calls: list[CovenantSpec] = []

    def review_spec(self, spec: CovenantSpec) -> SpecReviewDecision:
        self.calls.append(spec)
        return (
            self.queue.pop(0) if self.queue else SpecReviewDecision(accepted=True, confidence=1.0)
        )


class StubCompilerGraph:
    """Returns a fixed recompiled spec, recording the context it was given."""

    def __init__(self, spec: CovenantSpec | None, status: str = "compiled") -> None:
        self.spec = spec
        self.status = status
        self.contexts: list[str] = []

    def invoke(self, state):  # type: ignore[no-untyped-def]
        self.contexts.append(state.get("context", ""))

        class Outcome:
            specs = [self.spec] if self.spec is not None else []

        return {"status": self.status, "outcome": Outcome(), "validation_errors": []}


# --- structural safety: the decision type has no scored fields -------------------


def test_decision_type_has_no_number_verdict_or_evidence_fields():
    fields = set(SpecReviewDecision.model_fields)
    assert fields == {"accepted", "confidence", "objection", "issues"}
    assert "number" not in fields
    assert "verdict" not in fields
    assert "evidence_transaction_id" not in fields


@pytest.mark.parametrize(
    "smuggled",
    [
        {"number": 42},
        {"verdict": "complied"},
        {"evidence_transaction_id": "TX-1"},
    ],
)
def test_reviewer_cannot_smuggle_scored_fields(smuggled):
    with pytest.raises(ValidationError):
        SpecReviewDecision(accepted=True, confidence=0.9, **smuggled)


def test_objection_survives_round_trip():
    decision = SpecReviewDecision(
        accepted=False, confidence=0.4, objection="metric should be count, not sum"
    )
    assert decision.accepted is False
    assert "count" in decision.objection


# --- accepted path ---------------------------------------------------------------


def test_accepted_spec_is_marked_accepted_and_not_recompiled():
    reviewer = StubReviewer(SpecReviewDecision(accepted=True, confidence=0.95))
    graph = StubCompilerGraph(spec=None)
    service = SpecReviewService(reviewer=reviewer, compiler_graph=graph)

    result = service.review_and_maybe_recompile(build_spec(), candidate=object())

    assert result.spec.spec_trust == "accepted"
    assert result.spec.review_confidence == pytest.approx(0.95)
    assert result.recompiled is False
    assert graph.contexts == [], "accepted spec must not trigger recompilation"


def test_non_compiled_spec_skips_review_entirely():
    reviewer = StubReviewer()
    service = SpecReviewService(reviewer=reviewer)

    spec = build_spec(
        status="unsupported", condition=ConditionSpec(comparator="<=", threshold=None)
    )
    result = service.review_and_maybe_recompile(spec)

    assert reviewer.calls == [], "reviewer must not be called for non-compiled specs"
    assert result.spec is spec


# --- rejected path: bounded recompilation ----------------------------------------


def test_rejection_triggers_recompile_and_objection_reaches_the_compiler():
    revised = build_spec(covenant_id="COV-1", metric=MetricSpec(metric_type="count"))
    reviewer = StubReviewer(
        SpecReviewDecision(accepted=False, confidence=0.3, objection="should count, not sum"),
        SpecReviewDecision(accepted=True, confidence=0.88),
    )
    graph = StubCompilerGraph(spec=revised)
    service = SpecReviewService(reviewer=reviewer, compiler_graph=graph)

    result = service.review_and_maybe_recompile(
        build_spec(), candidate=object(), document_context="CTX"
    )

    assert result.recompiled is True
    assert result.spec.spec_trust == "revised"
    assert result.spec.metric.metric_type == "count", "revised spec must win"
    assert len(graph.contexts) == 1, "exactly one recompilation"
    assert "should count, not sum" in graph.contexts[0]
    assert "CTX" in graph.contexts[0], "original context must be preserved"


def test_second_rejection_yields_low_trust_but_still_returns_a_spec():
    revised = build_spec(covenant_id="COV-1", metric=MetricSpec(metric_type="count"))
    reviewer = StubReviewer(
        SpecReviewDecision(accepted=False, confidence=0.3, objection="first objection"),
        SpecReviewDecision(accepted=False, confidence=0.2, objection="second objection"),
    )
    graph = StubCompilerGraph(spec=revised)
    service = SpecReviewService(reviewer=reviewer, compiler_graph=graph)

    result = service.review_and_maybe_recompile(build_spec(), candidate=object())

    assert result.spec.spec_trust == "low"
    assert result.spec.review_objection == "second objection"
    assert result.spec is not None, "a rejected spec must still be evaluated, never dropped"


def test_recompilation_happens_at_most_once():
    """The hard cap from the architecture: recompile_count <= 1."""
    revised = build_spec(covenant_id="COV-1", metric=MetricSpec(metric_type="count"))
    reviewer = StubReviewer(
        *[SpecReviewDecision(accepted=False, confidence=0.1, objection="no") for _ in range(10)]
    )
    graph = StubCompilerGraph(spec=revised)
    service = SpecReviewService(reviewer=reviewer, compiler_graph=graph)

    service.review_and_maybe_recompile(build_spec(), candidate=object())

    assert len(graph.contexts) == 1, "must never recompile more than once"
    assert len(reviewer.calls) == 2, "at most two review passes per covenant"


def test_failed_recompilation_falls_back_to_original_spec_as_low_trust():
    reviewer = StubReviewer(
        SpecReviewDecision(accepted=False, confidence=0.3, objection="bad metric"),
    )
    graph = StubCompilerGraph(spec=None, status="failed_compilation")
    service = SpecReviewService(reviewer=reviewer, compiler_graph=graph)

    original = build_spec()
    result = service.review_and_maybe_recompile(original, candidate=object())

    assert result.spec.spec_trust == "low"
    assert result.spec.metric.metric_type == "sum", "original spec is retained"
    assert result.spec.review_objection == "bad metric"


def test_rejection_without_compiler_graph_degrades_to_low_trust():
    reviewer = StubReviewer(
        SpecReviewDecision(accepted=False, confidence=0.25, objection="mismatch")
    )
    service = SpecReviewService(reviewer=reviewer, compiler_graph=None)

    result = service.review_and_maybe_recompile(build_spec(), candidate=object())

    assert result.spec.spec_trust == "low"
    assert result.recompiled is False


# --- batch statistics ------------------------------------------------------------


def test_batch_stats_count_each_outcome_once():
    reviewer = StubReviewer(
        SpecReviewDecision(accepted=True, confidence=0.9),
        SpecReviewDecision(accepted=True, confidence=0.8),
    )
    service = SpecReviewService(reviewer=reviewer)

    _results, stats = service.review_batch([build_spec("COV-1"), build_spec("COV-2")])

    assert stats.reviewed == 2
    assert stats.accepted_first == 2
    assert stats.recompiled == 0
    assert stats.low_trust == 0
