from __future__ import annotations

import hashlib
import re
from collections import defaultdict
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
_PROHIBITION = re.compile(
    r"(?:запрещ|не\s+допуска|prohibited|not\s+allowed)", flags=re.IGNORECASE
)


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
        for block in self._logical_units(blocks):
            if not self._qualifies(block.text):
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

    @classmethod
    def _qualifies(cls, text: str) -> bool:
        if not _COVENANT_SIGNAL.search(text):
            return False
        return bool(re.search(r"\d", text) or _PROHIBITION.search(text))

    @classmethod
    def _logical_units(cls, blocks: list[DocumentBlock]) -> list[DocumentBlock]:
        """Assemble table rows and minimal adjacent text continuations before detection."""
        units: list[DocumentBlock] = []
        table_rows: dict[tuple[str, int, str, int], list[DocumentBlock]] = defaultdict(list)
        text_blocks: list[DocumentBlock] = []
        for block in blocks:
            if block.block_type == "table_cell" and block.table_id is not None and block.row_index is not None:
                table_rows[(block.document_id, block.page, block.table_id, block.row_index)].append(block)
            else:
                text_blocks.append(block)

        for row_blocks in table_rows.values():
            ordered = sorted(row_blocks, key=lambda item: (item.column_index or 0, item.block_id))
            text = " | ".join(item.text.strip() for item in ordered if item.text.strip())
            if not text:
                continue
            first = ordered[0]
            borrower_ids = sorted({borrower for item in ordered for borrower in item.borrower_ids})
            digest = hashlib.sha256(
                f"{first.document_id}:{first.page}:{first.table_id}:{first.row_index}:{text}".encode()
            ).hexdigest()[:20]
            units.append(
                first.model_copy(
                    update={
                        "block_id": f"table-row-{digest}",
                        "block_type": "table",
                        "text": text,
                        "borrower_ids": borrower_ids,
                        "confidence": min(item.confidence for item in ordered),
                    }
                )
            )

        ordered_text = sorted(text_blocks, key=lambda item: (item.document_id, item.page, item.block_id))
        units.extend(ordered_text)
        for previous, current in zip(ordered_text, ordered_text[1:], strict=False):
            if previous.document_id != current.document_id or previous.page != current.page:
                continue
            if previous.borrower_ids != current.borrower_ids:
                continue
            if cls._qualifies(previous.text) or cls._qualifies(current.text):
                continue
            combined = f"{previous.text.strip()} {current.text.strip()}".strip()
            if len(combined) > 1600 or not cls._qualifies(combined):
                continue
            digest = hashlib.sha256(
                f"{previous.block_id}:{current.block_id}:{combined}".encode()
            ).hexdigest()[:20]
            units.append(
                previous.model_copy(
                    update={
                        "block_id": f"joined-{digest}",
                        "text": combined,
                        "confidence": min(previous.confidence, current.confidence),
                    }
                )
            )
        return sorted(units, key=lambda item: (item.document_id, item.page, item.block_id))

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
