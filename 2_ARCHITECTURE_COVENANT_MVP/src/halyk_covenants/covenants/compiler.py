from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.llm.prompts import compiler_messages
from halyk_covenants.observability import trace_stage

from .detector import CovenantCandidate
from .identity import resolve_covenant_identity
from .validation import validate_compiled_spec


class CompiledCovenants(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specs: list[CovenantSpec]


class CompilationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal["straightforward", "ambiguous"]
    specs: list[CovenantSpec] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    raw_draft: dict[str, Any] | None = None


def apply_resolved_candidate_facts(
    spec: CovenantSpec, candidate: CovenantCandidate
) -> CovenantSpec:
    """Overlay deterministic facts while preserving a valid LLM-selected borrower subset.

    Candidate borrower IDs are an allowed scope, not a forced scope. The model may split a
    multi-borrower candidate into per-borrower specs, but it may never introduce an ID that was not
    resolved outside the model. Covenant identity is also generated/validated deterministically.
    """
    allowed = set(candidate.borrower_ids)
    requested = [borrower_id for borrower_id in spec.borrower_ids if borrower_id in allowed]
    if candidate.borrower_ids:
        if requested:
            borrower_ids = requested
        elif len(candidate.borrower_ids) == 1:
            borrower_ids = list(candidate.borrower_ids)
        else:
            borrower_ids = []
    else:
        borrower_ids = []

    covenant_id, covenant_group_id = resolve_covenant_identity(candidate, spec)
    return spec.model_copy(
        update={
            "covenant_id": covenant_id,
            "covenant_group_id": covenant_group_id,
            "raw_text": candidate.raw_text,
            "borrower_ids": borrower_ids,
            "source": candidate.source,
            "confidence": min(float(spec.confidence), float(candidate.confidence)),
        }
    )


class CovenantCompiler:
    def __init__(self, model: Any) -> None:
        self.model = model
        self.structured_model = model.with_structured_output(CompiledCovenants, method="json_mode")
        self.schema_json = json.dumps(
            CompiledCovenants.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )

    @trace_stage("covenant.compile", run_type="chain", tags=("preprocessing", "llm"))
    def compile(self, candidate: CovenantCandidate, context: str) -> CompilationOutcome:
        try:
            draft = self.structured_model.invoke(
                compiler_messages(
                    clause=candidate.raw_text,
                    context=context,
                    borrower_ids=candidate.borrower_ids,
                    schema_json=self.schema_json,
                )
            )
        except Exception as exc:
            return CompilationOutcome(
                route="ambiguous",
                validation_errors=[
                    f"structured output parsing failed: {type(exc).__name__}: {str(exc)[:500]}"
                ],
            )
        raw_draft = draft.model_dump(mode="json") if isinstance(draft, BaseModel) else draft
        try:
            envelope = (
                draft
                if isinstance(draft, CompiledCovenants)
                else CompiledCovenants.model_validate(draft)
            )
        except ValidationError as exc:
            return CompilationOutcome(
                route="ambiguous",
                validation_errors=[error["msg"] for error in exc.errors()],
                raw_draft=raw_draft if isinstance(raw_draft, dict) else None,
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
            or (["compiler returned no covenant specs"] if not envelope.specs else []),
            raw_draft=raw_draft if isinstance(raw_draft, dict) else None,
        )
