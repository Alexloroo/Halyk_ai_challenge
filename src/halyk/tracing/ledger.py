"""Trace ledger parsing, categorization, and account mapping."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from halyk.ledger import LedgerEntry

from .writer import TraceWriter

FIELDS = [
    "txn_id",
    "scenario_id",
    "day",
    "account_id",
    "counterparty",
    "description",
    "amount",
    "currency",
    "category",
    "is_related_party",
    "is_unrestricted_transfer",
    "audit_reclassified",
    "defects",
]


def entry_row(entry: LedgerEntry) -> dict[str, object]:
    return {
        "txn_id": entry.txn_id,
        "scenario_id": entry.scenario_id,
        "day": entry.day,
        "account_id": entry.account_id,
        "counterparty": entry.counterparty,
        "description": entry.description,
        "amount": entry.amount,
        "currency": entry.currency,
        "category": entry.category,
        "is_related_party": entry.is_related_party,
        "is_unrestricted_transfer": entry.is_unrestricted_transfer,
        "audit_reclassified": entry.audit_reclassified,
        "defects": ";".join(entry.defects),
    }


def _write_entries(
    writer: TraceWriter,
    stage: str,
    name: str,
    entries: Iterable[LedgerEntry],
) -> list[LedgerEntry]:
    materialized = list(entries)
    writer.write_csv(stage, name, [entry_row(entry) for entry in materialized], fieldnames=FIELDS)
    return materialized


def trace_loaded(writer: TraceWriter, entries: list[LedgerEntry]) -> None:
    _write_entries(writer, "02_ledger_loaded", "ledger.csv", entries)
    defects = Counter(defect for entry in entries for defect in entry.defects)
    writer.write_json(
        "02_ledger_loaded",
        "summary.json",
        {"rows": len(entries), "defects": defects},
    )
    writer.update_stage("02_ledger_loaded", rows=len(entries), defects=sum(defects.values()))


def trace_categorized(writer: TraceWriter, entries: list[LedgerEntry]) -> None:
    _write_entries(writer, "03_ledger_categorized", "ledger.csv", entries)
    counts = Counter(entry.category.value for entry in entries)
    writer.write_json(
        "03_ledger_categorized",
        "category_counts.json",
        {"rows": len(entries), "categories": counts},
    )
    writer.update_stage(
        "03_ledger_categorized", rows=len(entries), categories=len(counts)
    )


def trace_account_mapping(writer: TraceWriter, accounts: dict[str, str]) -> None:
    writer.write_json("06_account_mapping", "accounts.json", accounts)
    writer.update_stage("06_account_mapping", scenarios=len(accounts), status="completed")


def trace_scenario_entries(
    writer: TraceWriter,
    stage: str,
    scenario_id: str,
    name: str,
    entries: list[LedgerEntry],
) -> None:
    _write_entries(writer, stage, f"{scenario_id}/{name}", entries)
