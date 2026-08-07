"""Trace scenario-level document selection and deterministic interpretation."""

from __future__ import annotations

from typing import Any

from halyk.audit import AuditAdjustment
from halyk.docs import Document
from halyk.parties import RelatedParties
from halyk.rules import Rule

from .documents import document_record
from .ledger import trace_scenario_entries
from .writer import TraceWriter


def trace_selected(
    writer: TraceWriter,
    scenario_id: str,
    account_id: str,
    *,
    agreement: Document | None,
    kyc: Document | None,
    audit_documents: list[Document],
) -> None:
    writer.write_json(
        "07_documents_selected",
        f"{scenario_id}.json",
        {
            "scenario_id": scenario_id,
            "account_id": account_id,
            "agreement": document_record(agreement) if agreement else None,
            "kyc": document_record(kyc) if kyc else None,
            "audit_documents": [document_record(document) for document in audit_documents],
        },
    )


def trace_audit_input(
    writer: TraceWriter,
    scenario_id: str,
    entries: list[Any],
) -> None:
    trace_scenario_entries(writer, "08_audit_and_fx", scenario_id, "ledger_before.csv", entries)


def trace_audit_output(
    writer: TraceWriter,
    scenario_id: str,
    adjustments: list[AuditAdjustment],
    fx_rates: dict[str, object],
    entries: list[Any],
) -> None:
    writer.write_json(
        "08_audit_and_fx",
        f"{scenario_id}/transformations.json",
        {"adjustments": adjustments, "fx_rates": fx_rates},
    )
    trace_scenario_entries(writer, "08_audit_and_fx", scenario_id, "ledger_after.csv", entries)


def trace_parties(
    writer: TraceWriter,
    scenario_id: str,
    parties: RelatedParties | None,
    entries: list[Any],
) -> None:
    writer.write_json(
        "09_related_parties",
        f"{scenario_id}.json",
        {
            "result": parties,
            "flagged_txn_ids": [entry.txn_id for entry in entries if entry.is_related_party],
            "unrestricted_txn_ids": [
                entry.txn_id for entry in entries if entry.is_unrestricted_transfer
            ],
        },
    )


def trace_rules(writer: TraceWriter, scenario_id: str, rules: dict[str, Rule]) -> None:
    writer.write_json("10_rules", f"{scenario_id}.json", rules)
