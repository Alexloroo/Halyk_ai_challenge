from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from halyk_covenants.domain import CovenantSpec

    from .detector import CovenantCandidate

_EXPLICIT_CODE = re.compile(r"\bCOV-[A-Z0-9-]+\b", flags=re.IGNORECASE)
_CLAUSE_REFERENCE = re.compile(
    r"(?:clause|section|пункт|п\.)\s*(\d+(?:\.\d+)*)",
    flags=re.IGNORECASE,
)


def resolve_covenant_identity(
    candidate: CovenantCandidate,
    spec: CovenantSpec,
) -> tuple[str, str | None]:
    """Return authoritative version/family identifiers without trusting model-generated IDs.

    Explicit contract codes stay human-readable and backwards compatible. Otherwise the version ID
    is a stable hash of the source candidate plus executable semantics. A clause/section reference
    becomes a cross-document family key when one is explicit in the source text.
    """
    code_match = _EXPLICIT_CODE.search(candidate.raw_text)
    if code_match is not None:
        code = code_match.group(0).upper()
        return code, code

    semantic_payload = {
        "candidate_id": candidate.candidate_id,
        "metric": spec.metric.model_dump(mode="json"),
        "condition": spec.condition.model_dump(mode="json"),
        "filters": [item.model_dump(mode="json") for item in spec.transaction_filters],
        "exclusions": [item.model_dump(mode="json") for item in spec.exclusions],
        "group_by": spec.group_by,
        "date_field": spec.date_field,
        "time_window": spec.time_window.model_dump(mode="json") if spec.time_window else None,
        "effective_from": spec.effective_from.isoformat() if spec.effective_from else None,
        "effective_to": spec.effective_to.isoformat() if spec.effective_to else None,
    }
    digest = hashlib.sha256(
        json.dumps(semantic_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    version_id = f"COV-AUTO-{digest.upper()}"

    reference = _CLAUSE_REFERENCE.search(candidate.raw_text)
    family_id = f"CLAUSE-{reference.group(1)}" if reference else None
    return version_id, family_id
