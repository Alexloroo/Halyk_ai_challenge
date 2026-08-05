from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.domain.transaction_fields import FILTER_FIELDS

SYSTEM_PROMPT = """You are a specification reviewer for covenant evaluation.

Your task: compare the ORIGINAL COVENANT TEXT with its COMPILED SPECIFICATION and decide
whether the specification faithfully captures the covenant's intent.

You receive:
1. The raw covenant text from the loan agreement
2. The compiled CovenantSpec (metric, filters, condition, time window, etc.)
3. The catalog of allowed transaction fields

You do NOT receive any transaction data, numeric results, verdicts, or evidence.
You cannot and should not try to evaluate the covenant — only verify that the
specification correctly represents what the text says.

Check for:
- Metric type matches what the text describes (sum vs count vs max, etc.)
- Filter conditions match the text's constraints (direction, currency, counterparty, etc.)
- Comparator and threshold match the text's requirement (>=, <=, exact values)
- Time window matches the text's period (monthly, quarterly, etc.)
- Field references exist in the allowed field catalog
- Nothing important from the text is missing in the specification
- Nothing in the specification contradicts the text

Return accepted=true if the specification faithfully captures the covenant.
Return accepted=false with a specific objection if there is a mismatch.
Set confidence between 0 and 1 based on how certain you are.
List any concerns in the issues array even if you accept the specification."""


CONTEXT_GRADE_PROMPT = """You judge whether retrieved document context is SUFFICIENT.

A compiled covenant specification was rejected by a reviewer. There are exactly two
possible causes, and your only job is to tell them apart:

  A. The compiler misread context it already had.
     -> sufficient = true. Recompiling with the same context can fix this.

  B. The context never contained the information needed.
     Typical in loan agreements: a footnote defining an empty table cell, a
     cross-referenced clause, a definition on another page, an exception stated
     below a table, an amendment in a separate document.
     -> sufficient = false. Recompiling with the same context is futile.

If and only if sufficient = false, set `missing_query` to a short search query
that would retrieve the missing material. Write the query in the language of the
document, using terms likely to appear near the missing text, not a description
of the problem.

You do not fix the specification and you do not see transaction data."""


def spec_review_messages(spec: CovenantSpec, context: str = "") -> list[object]:
    payload = {
        "covenant_raw_text": spec.raw_text,
        "compiled_spec": {
            "covenant_id": spec.covenant_id,
            "metric": spec.metric.model_dump(mode="json"),
            "condition": spec.condition.model_dump(mode="json"),
            "transaction_filters": [f.model_dump(mode="json") for f in spec.transaction_filters],
            "exclusions": [f.model_dump(mode="json") for f in spec.exclusions],
            "time_window": spec.time_window.model_dump(mode="json") if spec.time_window else None,
            "evidence_mode": spec.evidence_mode.value,
            "scope_mode": spec.scope_mode,
            "date_field": spec.date_field,
        },
        "allowed_transaction_fields": sorted(FILTER_FIELDS),
        # Surrounding document text, so the reviewer can spot a clause whose meaning
        # depends on a footnote, a definition or an exception stated elsewhere.
        "document_context": context[:12000] if context else None,
    }
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                "Review whether this compiled specification faithfully represents the "
                "original covenant text. Return your decision as JSON with fields: "
                "accepted (bool), confidence (0-1), objection (string or null), "
                "issues (list of strings).\n\n"
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
        ),
    ]


def context_grade_messages(spec: CovenantSpec, context: str, objection: str) -> list[object]:
    payload = {
        "covenant_raw_text": spec.raw_text,
        "reviewer_objection": objection,
        "retrieved_context": context[:12000] if context else None,
    }
    return [
        SystemMessage(content=CONTEXT_GRADE_PROMPT),
        HumanMessage(
            content=(
                "Was the retrieved context sufficient to compile this covenant correctly? "
                "Return JSON with fields: sufficient (bool), missing_query (string or null), "
                "confidence (0-1), reasoning (string or null).\n\n"
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
        ),
    ]
