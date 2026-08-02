from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.domain import DocumentBlock, SourceRef
from halyk_covenants.observability import trace_stage

_COVENANT_SIGNAL = re.compile(
    r"(?:must|shall|may\s+not|must\s+not|not\s+exceed|no\s+more|at\s+least|"
    r"не\s+более|не\s+менее|не\s+долж|запрещ|не\s+допуска|лимит|превыш|≤|≥)",
    flags=re.IGNORECASE,
)
_INDEPENDENT_SPLIT = re.compile(
    r"\s*[,;]\s*(?:and|и)\s+(?=(?:no\s+more|not\s+more|at\s+least|each|"
    r"не\s+более|не\s+менее|кажд))",
    flags=re.IGNORECASE,
)
_COVENANT_CODE = re.compile(r"\bCOV-[A-Z0-9-]+\b", flags=re.IGNORECASE)


class CovenantCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    raw_text: str
    ordinal: int = Field(ge=1)
    borrower_ids: list[str] = Field(default_factory=list)
    source: SourceRef
    confidence: Decimal = Field(ge=0, le=1)


class CovenantDetector:
    @trace_stage("covenant.detect", run_type="chain", tags=("preprocessing", "covenant"))
    def detect(self, blocks: list[DocumentBlock]) -> list[CovenantCandidate]:
        candidates: list[CovenantCandidate] = []
        ordinal = 0
        for block in blocks:
            if not _COVENANT_SIGNAL.search(block.text):
                continue
            if not re.search(r"\d", block.text) and not re.search(
                r"(?:запрещ|не\s+допуска|prohibited|not\s+allowed)",
                block.text,
                flags=re.IGNORECASE,
            ):
                continue
            clauses = [
                part.strip() for part in _INDEPENDENT_SPLIT.split(block.text) if part.strip()
            ]
            for clause in clauses:
                ordinal += 1
                digest = hashlib.sha256(
                    f"{block.document_id}:{block.page}:{block.block_id}:{ordinal}:{clause}".encode()
                ).hexdigest()[:16]
                candidates.append(
                    CovenantCandidate(
                        candidate_id=f"candidate-{digest}",
                        raw_text=clause,
                        ordinal=ordinal,
                        borrower_ids=list(block.borrower_ids),
                        source=block.source,
                        confidence=block.confidence,
                    )
                )
        return self._deduplicate_explicit_codes(candidates)

    @staticmethod
    def _deduplicate_explicit_codes(
        candidates: list[CovenantCandidate],
    ) -> list[CovenantCandidate]:
        deduplicated: list[CovenantCandidate] = []
        code_indexes: dict[str, int] = {}
        for candidate in candidates:
            match = _COVENANT_CODE.search(candidate.raw_text)
            if match is None:
                deduplicated.append(candidate)
                continue
            code = match.group(0).upper()
            existing_index = code_indexes.get(code)
            if existing_index is None:
                code_indexes[code] = len(deduplicated)
                deduplicated.append(candidate)
                continue
            existing = deduplicated[existing_index]
            if len(candidate.raw_text) > len(existing.raw_text):
                deduplicated[existing_index] = candidate
        return [
            candidate.model_copy(update={"ordinal": ordinal})
            for ordinal, candidate in enumerate(deduplicated, start=1)
        ]
