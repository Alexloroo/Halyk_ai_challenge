from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

SYSTEM_PROMPT = """You are a covenant compiler. Convert contract clauses into CovenantSpec JSON.
Extract every independently testable condition. Preserve exact comparator boundaries, filters,
exclusions, periods, units, currencies, borrower scope, evidence behavior and effective dates.
Compile conditions from CLAUSE only; use RELEVANT_CONTEXT only for definitions, dates and schema
mapping. Do not compile other covenant clauses found only in RELEVANT_CONTEXT.
Use only transaction fields present in the supplied semantic catalog. Never calculate a metric,
invent an identifier, infer an FX conversion, or return prose. Unsupported rules must be marked
unsupported instead of approximated."""


def compiler_messages(
    *, clause: str, context: str, borrower_ids: list[str], schema_json: str
) -> list[object]:
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"BORROWER_IDS: {borrower_ids}\n"
                f"CLAUSE:\n{clause}\n\n"
                f"RELEVANT_CONTEXT:\n{context or '(none)'}\n\n"
                "Return exactly one JSON object matching this schema. The top-level "
                "key must be `specs`, even when there is only one covenant.\n"
                f"JSON_SCHEMA:\n{schema_json}"
            )
        ),
    ]
