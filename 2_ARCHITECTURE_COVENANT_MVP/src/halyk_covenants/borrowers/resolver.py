from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz.fuzz import WRatio, partial_ratio

from halyk_covenants.domain import Borrower
from halyk_covenants.observability import trace_stage

from .normalization import (
    normalize_identifier_key,
    normalize_identifier_value,
    normalize_name,
)


class BorrowerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)


class BorrowerCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    borrower_id: str
    score: float
    matched_by: str


class BorrowerResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "resolved_exact",
        "resolved_alias",
        "resolved_fuzzy",
        "ambiguous",
        "unresolved",
    ]
    borrower_ids: list[str] = Field(default_factory=list)
    candidates: list[BorrowerCandidate] = Field(default_factory=list)
    matched_by: str | None = None


class BorrowerResolver:
    def __init__(
        self,
        borrowers: list[Borrower],
        *,
        fuzzy_threshold: float = 85,
        ambiguity_margin: float = 3,
    ) -> None:
        self.borrowers = borrowers
        self.fuzzy_threshold = fuzzy_threshold
        self.ambiguity_margin = ambiguity_margin

    @trace_stage("borrower.resolve", run_type="tool", tags=("preprocessing", "borrower"))
    def resolve(self, claim: BorrowerClaim) -> BorrowerResolution:
        exact = self._resolve_identifier(claim)
        if exact is not None:
            return exact

        if not claim.name or not normalize_name(claim.name):
            return BorrowerResolution(status="unresolved")

        exact_name = self._resolve_exact_name(claim.name)
        if exact_name is not None:
            return exact_name

        alias = self._resolve_alias(claim.name)
        if alias is not None:
            return alias

        return self._resolve_fuzzy(claim.name)

    def _resolve_identifier(self, claim: BorrowerClaim) -> BorrowerResolution | None:
        for claim_key, claim_value in claim.identifiers.items():
            key = normalize_identifier_key(claim_key)
            value = normalize_identifier_value(claim_value)
            matches = [
                borrower
                for borrower in self.borrowers
                if any(
                    normalize_identifier_key(candidate_key) == key
                    and normalize_identifier_value(candidate_value) == value
                    for candidate_key, candidate_value in borrower.identifiers.items()
                )
            ]
            if len(matches) == 1:
                matched_by = f"identifier:{claim_key}"
                return BorrowerResolution(
                    status="resolved_exact",
                    borrower_ids=[matches[0].borrower_id],
                    matched_by=matched_by,
                    candidates=[
                        BorrowerCandidate(
                            borrower_id=matches[0].borrower_id,
                            score=100,
                            matched_by=matched_by,
                        )
                    ],
                )
            if len(matches) > 1:
                return BorrowerResolution(
                    status="ambiguous",
                    candidates=[
                        BorrowerCandidate(
                            borrower_id=borrower.borrower_id,
                            score=100,
                            matched_by=f"identifier:{claim_key}",
                        )
                        for borrower in matches
                    ],
                )
        return None

    def _resolve_exact_name(self, name: str) -> BorrowerResolution | None:
        normalized = normalize_name(name)
        matches = [
            borrower
            for borrower in self.borrowers
            if borrower.canonical_name and normalize_name(borrower.canonical_name) == normalized
        ]
        if len(matches) == 1:
            return BorrowerResolution(
                status="resolved_exact",
                borrower_ids=[matches[0].borrower_id],
                matched_by="normalized_name",
            )
        if len(matches) > 1:
            return BorrowerResolution(
                status="ambiguous",
                candidates=[
                    BorrowerCandidate(
                        borrower_id=borrower.borrower_id,
                        score=100,
                        matched_by="normalized_name",
                    )
                    for borrower in matches
                ],
            )
        return None

    def _resolve_alias(self, name: str) -> BorrowerResolution | None:
        normalized = normalize_name(name)
        matches = [
            borrower
            for borrower in self.borrowers
            if any(normalize_name(alias) == normalized for alias in borrower.aliases)
        ]
        if len(matches) == 1:
            return BorrowerResolution(
                status="resolved_alias",
                borrower_ids=[matches[0].borrower_id],
                matched_by="alias",
            )
        if len(matches) > 1:
            return BorrowerResolution(
                status="ambiguous",
                candidates=[
                    BorrowerCandidate(
                        borrower_id=borrower.borrower_id,
                        score=100,
                        matched_by="alias",
                    )
                    for borrower in matches
                ],
            )
        return None

    def _resolve_fuzzy(self, name: str) -> BorrowerResolution:
        query = normalize_name(name, strip_legal_form=True)
        candidates: list[BorrowerCandidate] = []
        for borrower in self.borrowers:
            names = [borrower.canonical_name, *borrower.aliases]
            scored = []
            for candidate in names:
                if not candidate:
                    continue
                normalized = normalize_name(candidate, strip_legal_form=True)
                # Prefix-like legal-name variants (Trade/Trading) are deliberately
                # treated as close so they require adjudication instead of a guess.
                scored.append(max(WRatio(query, normalized), partial_ratio(query, normalized)))
            if scored:
                candidates.append(
                    BorrowerCandidate(
                        borrower_id=borrower.borrower_id,
                        score=float(max(scored)),
                        matched_by="fuzzy_name",
                    )
                )

        candidates.sort(key=lambda candidate: (-candidate.score, candidate.borrower_id))
        if not candidates or candidates[0].score < self.fuzzy_threshold:
            return BorrowerResolution(status="unresolved", candidates=candidates[:3])

        if (
            len(candidates) > 1
            and candidates[0].score - candidates[1].score <= self.ambiguity_margin
        ):
            return BorrowerResolution(status="ambiguous", candidates=candidates[:3])

        return BorrowerResolution(
            status="resolved_fuzzy",
            borrower_ids=[candidates[0].borrower_id],
            candidates=candidates[:3],
            matched_by="fuzzy_name",
        )
