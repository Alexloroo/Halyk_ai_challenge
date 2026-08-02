from __future__ import annotations

from datetime import date
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from halyk_covenants.domain import CovenantResult, CovenantSpec
from halyk_covenants.evaluators import EvaluationService
from halyk_covenants.observability import trace_stage

from .verifier import ResultVerifier

AUTHORIZED_TOP_LEVEL = frozenset(
    {"spec", "borrower_mapping", "period_mapping", "evidence_strategy"}
)
AUTHORIZED_SPEC_FIELDS = frozenset(
    {
        "borrower_ids",
        "scope_mode",
        "metric",
        "condition",
        "transaction_filters",
        "exclusions",
        "group_by",
        "date_field",
        "time_window",
        "evidence_mode",
        "effective_from",
        "effective_to",
    }
)


class RepairProposer(Protocol):
    def propose(self, state: dict[str, object]) -> dict[str, object]: ...


class RepairState(TypedDict, total=False):
    spec: CovenantSpec
    result: CovenantResult
    transaction_snapshot_hash: str
    evaluation_service: EvaluationService
    verifier: ResultVerifier
    evaluation_date: date
    patch: dict[str, object]
    attempt: int
    status: str
    errors: list[str]


class RepairGraph:
    """Bounded verifier repair loop which can patch interpretation, never source facts."""

    def __init__(self, *, proposer: RepairProposer, max_attempts: int = 2) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.proposer = proposer
        self.max_attempts = max_attempts
        self.graph = self._build()

    def _build(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(RepairState)
        builder.add_node("propose", self._propose)
        builder.add_node("apply", self._apply)
        builder.add_edge(START, "propose")
        builder.add_conditional_edges(
            "propose", self._after_propose, {"apply": "apply", "done": END}
        )
        builder.add_conditional_edges("apply", self._after_apply, {"retry": "propose", "done": END})
        return builder.compile()

    @trace_stage("verification.repair.propose", run_type="chain", tags=("verification", "llm"))
    def _propose(self, state: RepairState) -> RepairState:
        attempt = state.get("attempt", 0) + 1
        patch = self.proposer.propose(dict(state))
        unauthorized = sorted(set(patch) - AUTHORIZED_TOP_LEVEL)
        nested_spec = patch.get("spec")
        if isinstance(nested_spec, dict):
            unauthorized.extend(
                f"spec.{field}" for field in sorted(set(nested_spec) - AUTHORIZED_SPEC_FIELDS)
            )
        if unauthorized:
            return {
                "attempt": attempt,
                "patch": patch,
                "status": "rejected_unauthorized_patch",
                "errors": [f"unauthorized repair fields: {', '.join(unauthorized)}"],
            }
        return {"attempt": attempt, "patch": patch, "status": "proposed"}

    @trace_stage("verification.repair.apply", run_type="tool", tags=("verification",))
    def _apply(self, state: RepairState) -> RepairState:
        try:
            patched = _apply_patch(state["spec"], state["patch"])
        except (ValidationError, ValueError, TypeError) as exc:
            status = (
                "repair_exhausted" if state["attempt"] >= self.max_attempts else "invalid_patch"
            )
            return {"status": status, "errors": [str(exc)]}

        updates: RepairState = {"spec": patched, "status": "patched", "errors": []}
        service = state.get("evaluation_service")
        if service is not None:
            result = service.evaluate(
                patched,
                state["result"].borrower_id,
                state.get("evaluation_date"),
            )
            verification = state.get("verifier", ResultVerifier()).verify_pair(patched, result)
            updates["result"] = result
            if verification.valid:
                updates["status"] = "repaired"
            elif state["attempt"] >= self.max_attempts:
                updates["status"] = "repair_exhausted"
                updates["errors"] = [issue.message for issue in verification.issues]
            else:
                updates["status"] = "invalid_patch"
        return updates

    def _after_propose(self, state: RepairState) -> str:
        return "done" if state.get("status") == "rejected_unauthorized_patch" else "apply"

    def _after_apply(self, state: RepairState) -> str:
        return "retry" if state.get("status") == "invalid_patch" else "done"

    @trace_stage("verification.repair.graph", run_type="chain", tags=("verification", "langgraph"))
    def invoke(self, initial: dict[str, object]) -> RepairState:
        return self.graph.invoke(initial, config={"recursion_limit": self.max_attempts * 2 + 4})


def _apply_patch(spec: CovenantSpec, patch: dict[str, object]) -> CovenantSpec:
    payload = spec.model_dump(mode="python")
    spec_patch = patch.get("spec", {})
    if spec_patch and not isinstance(spec_patch, dict):
        raise TypeError("spec patch must be an object")
    payload.update(spec_patch)

    borrower_mapping = patch.get("borrower_mapping", {})
    if borrower_mapping:
        unsupported = (
            set(borrower_mapping) - {"borrower_ids", "scope_mode"}
            if isinstance(borrower_mapping, dict)
            else {"invalid_mapping"}
        )
        if unsupported:
            raise ValueError("borrower_mapping contains unsupported fields")
        payload.update(borrower_mapping)

    period_mapping = patch.get("period_mapping", {})
    if period_mapping:
        allowed = {"time_window", "date_field", "effective_from", "effective_to"}
        if not isinstance(period_mapping, dict) or set(period_mapping) - allowed:
            raise ValueError("period_mapping contains unsupported fields")
        payload.update(period_mapping)

    evidence_strategy = patch.get("evidence_strategy", {})
    if evidence_strategy:
        if not isinstance(evidence_strategy, dict) or set(evidence_strategy) != {"evidence_mode"}:
            raise ValueError("evidence_strategy may contain only evidence_mode")
        payload.update(evidence_strategy)
    return CovenantSpec.model_validate(payload)
