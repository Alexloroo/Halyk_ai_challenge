from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.domain import CovenantResult, CovenantSpec

ConfidenceLevel = Literal["high", "medium", "low", "unreliable"]


class AnswerConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    borrower_id: str
    covenant_id: str
    level: ConfidenceLevel
    triage_rank: int = Field(ge=1)
    flags: list[str] = Field(default_factory=list)
    spec_trust: str = "accepted"
    review_objection: str | None = None


def compute_confidence(
    result: CovenantResult,
    spec: CovenantSpec | None,
    verification_flags: set[str] | None = None,
) -> ConfidenceLevel:
    flags = verification_flags or set()

    if "dual_path_mismatch" in flags or result.status == "failed":
        return "unreliable"

    spec_trust = spec.spec_trust if spec else "accepted"

    if spec_trust == "low" or "evidence_mismatch" in flags:
        return "low"

    if (
        spec_trust == "revised"
        or result.status == "partial"
        or "empty_input_rows" in flags
    ):
        return "medium"

    review_conf = spec.review_confidence if spec else None
    compiler_conf = spec.confidence if spec else None

    if (
        spec_trust == "accepted"
        and not flags
        and (review_conf is None or review_conf >= 0.70)
        and (compiler_conf is None or compiler_conf >= 0.70)
    ):
        return "high"

    return "medium"


_LEVEL_ORDER = {"unreliable": 0, "low": 1, "medium": 2, "high": 3}


def build_confidence_report(
    results: list[CovenantResult],
    specs: dict[str, CovenantSpec] | None = None,
    verification_flags: dict[tuple[str, str], set[str]] | None = None,
) -> list[AnswerConfidence]:
    specs = specs or {}
    verification_flags = verification_flags or {}

    scored: list[tuple[ConfidenceLevel, CovenantResult, CovenantSpec | None, set[str]]] = []
    for result in results:
        pair = (result.borrower_id, result.covenant_id)
        spec = specs.get(result.covenant_id)
        flags = verification_flags.get(pair, set())
        scored.append((compute_confidence(result, spec, flags), result, spec, flags))

    # Worst answers first, so a human working top-down spends attention where it pays.
    scored.sort(
        key=lambda item: (
            _LEVEL_ORDER.get(item[0], 2),
            item[1].borrower_id,
            item[1].covenant_id,
        )
    )

    return [
        AnswerConfidence(
            borrower_id=result.borrower_id,
            covenant_id=result.covenant_id,
            level=level,
            triage_rank=rank,
            flags=sorted(flags),
            spec_trust=spec.spec_trust if spec else "accepted",
            review_objection=spec.review_objection if spec else None,
        )
        for rank, (level, result, spec, flags) in enumerate(scored, start=1)
    ]
