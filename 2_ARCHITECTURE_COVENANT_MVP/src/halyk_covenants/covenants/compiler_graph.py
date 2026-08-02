from __future__ import annotations

import json
from typing import Any, Protocol, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from halyk_covenants.observability import trace_stage

from .compiler import (
    CompilationOutcome,
    CompiledCovenants,
    apply_resolved_candidate_facts,
)
from .detector import CovenantCandidate
from .validation import validate_compiled_spec


class CompilerProtocol(Protocol):
    def compile(self, candidate: CovenantCandidate, context: str) -> CompilationOutcome: ...


class RepairerProtocol(Protocol):
    def repair(
        self,
        *,
        candidate: CovenantCandidate,
        context: str,
        previous: CompilationOutcome,
        attempt: int,
    ) -> CompilationOutcome: ...


class CompilerState(TypedDict, total=False):
    candidate: CovenantCandidate
    context: str
    attempt: int
    outcome: CompilationOutcome
    status: str
    validation_errors: list[str]


class LangChainCompilerRepairer:
    """Schema-only repairer. It has no access to transaction values or verdicts."""

    def __init__(self, model: Any) -> None:
        self.structured_model = model.with_structured_output(CompiledCovenants, method="json_mode")
        self.schema_json = json.dumps(
            CompiledCovenants.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )

    @trace_stage("covenant.compiler.repair_llm", run_type="chain", tags=("preprocessing", "llm"))
    def repair(
        self,
        *,
        candidate: CovenantCandidate,
        context: str,
        previous: CompilationOutcome,
        attempt: int,
    ) -> CompilationOutcome:
        try:
            response = self.structured_model.invoke(
                [
                    SystemMessage(
                        content=(
                            "Repair only the CovenantSpec JSON using the validation errors. "
                            "Do not calculate, change source transactions, or invent identifiers."
                        )
                    ),
                    HumanMessage(
                        content=(
                            f"ATTEMPT: {attempt}\nCLAUSE: {candidate.raw_text}\n"
                            f"BORROWERS: {candidate.borrower_ids}\nCONTEXT: {context}\n"
                            f"ERRORS: {previous.validation_errors}\nDRAFT: {previous.raw_draft}\n"
                            "Return exactly one JSON object with top-level key `specs` matching "
                            f"this schema:\n{self.schema_json}"
                        )
                    ),
                ]
            )
        except Exception as exc:
            return CompilationOutcome(
                route="ambiguous",
                validation_errors=[
                    f"structured repair parsing failed: {type(exc).__name__}: {str(exc)[:500]}"
                ],
            )
        envelope = (
            response
            if isinstance(response, CompiledCovenants)
            else CompiledCovenants.model_validate(response)
        )
        specs = [apply_resolved_candidate_facts(spec, candidate) for spec in envelope.specs]
        errors: list[str] = []
        for spec in specs:
            errors.extend(
                validate_compiled_spec(
                    spec,
                    clause=candidate.raw_text,
                    allowed_borrower_ids=candidate.borrower_ids,
                )
            )
        return CompilationOutcome(
            route="ambiguous" if errors or not envelope.specs else "straightforward",
            specs=specs,
            validation_errors=errors
            or (["repair returned no specs"] if not envelope.specs else []),
            raw_draft=envelope.model_dump(mode="json"),
        )


class CompilerGraph:
    def __init__(
        self,
        *,
        compiler: CompilerProtocol,
        repairer: RepairerProtocol,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.compiler = compiler
        self.repairer = repairer
        self.max_attempts = max_attempts
        self.graph = self._build_graph()

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(CompilerState)
        builder.add_node("compile", self._compile_node)
        builder.add_node("repair", self._repair_node)
        builder.add_edge(START, "compile")
        builder.add_conditional_edges(
            "compile",
            self._route,
            {"done": END, "repair": "repair"},
        )
        builder.add_conditional_edges(
            "repair",
            self._route,
            {"done": END, "repair": "repair"},
        )
        return builder.compile()

    @trace_stage("covenant.compiler.graph.compile", run_type="chain", tags=("preprocessing",))
    def _compile_node(self, state: CompilerState) -> CompilerState:
        outcome = self.compiler.compile(state["candidate"], state.get("context", ""))
        status = "compiled" if outcome.route == "straightforward" else "ambiguous"
        return {
            "outcome": outcome,
            "status": status,
            "attempt": state.get("attempt", 0),
            "validation_errors": outcome.validation_errors,
        }

    @trace_stage("covenant.compiler.graph.repair", run_type="chain", tags=("preprocessing",))
    def _repair_node(self, state: CompilerState) -> CompilerState:
        attempt = state.get("attempt", 0) + 1
        outcome = self.repairer.repair(
            candidate=state["candidate"],
            context=state.get("context", ""),
            previous=state["outcome"],
            attempt=attempt,
        )
        if outcome.route == "straightforward":
            status = "compiled"
        elif attempt >= self.max_attempts:
            status = "failed_compilation"
        else:
            status = "ambiguous"
        return {
            "outcome": outcome,
            "status": status,
            "attempt": attempt,
            "validation_errors": outcome.validation_errors,
        }

    def _route(self, state: CompilerState) -> str:
        return "done" if state.get("status") in {"compiled", "failed_compilation"} else "repair"

    @trace_stage("covenant.compiler.graph", run_type="chain", tags=("preprocessing", "langgraph"))
    def invoke(self, initial: CompilerState) -> CompilerState:
        return self.graph.invoke(initial, config={"recursion_limit": self.max_attempts + 5})
