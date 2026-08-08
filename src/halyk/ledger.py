"""The transaction ledger.

One CSV holds every borrower. The scenario is not a column — it is the second
segment of txn_id, and that is the only link between a document and a template
cell. Rows for accounts outside the template are noise and are marked as such
rather than dropped, so a wrong scenario mapping shows up as a count instead of
silently shrinking the data.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .categorize import Category


@dataclass
class LedgerEntry:
    txn_id: str
    scenario_id: str
    day: date
    account_id: str
    counterparty: str
    description: str
    amount: Decimal | None  # signed, as recorded: expenses negative
    currency: str
    category: Category = Category.UNKNOWN
    is_related_party: bool = False
    is_unrestricted_transfer: bool = False
    audit_reclassified: bool = False
    audit_corrected: bool = False
    audit_excluded: bool = False
    fx_converted: bool = False
    defects: list[str] = field(default_factory=list)

    @property
    def magnitude(self) -> Decimal:
        """Absolute value. `actual` is always positive, even for spending."""
        return abs(self.amount) if self.amount is not None else Decimal(0)

    @property
    def is_outflow(self) -> bool:
        return self.amount is not None and self.amount < 0

    @property
    def is_inflow(self) -> bool:
        return self.amount is not None and self.amount > 0

    @property
    def usable(self) -> bool:
        return self.amount is not None


def scenario_of(txn_id: str) -> str:
    """TXN-P1-0039 -> P1. The case states this is the borrower link."""
    parts = txn_id.split("-")
    return parts[1] if len(parts) >= 3 else ""


def _parse_amount(raw: str) -> tuple[Decimal | None, list[str]]:
    text = (raw or "").strip()
    if not text:
        return None, ["missing_amount"]
    try:
        return Decimal(text), []
    except InvalidOperation:
        return None, ["malformed_amount"]


def _parse_day(raw: str) -> tuple[date | None, list[str]]:
    try:
        return date.fromisoformat((raw or "").strip()), []
    except ValueError:
        return None, ["malformed_date"]


def load_ledger(path: Path, scenarios: set[str] | None = None) -> list[LedgerEntry]:
    """Read the ledger. With `scenarios`, keep only those; otherwise keep all.

    Nothing is silently discarded: a row that cannot be parsed keeps its defect
    markers and travels on, and the rule decides whether it matters.
    """
    entries: list[LedgerEntry] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            txn_id = (row.get("txn_id") or "").strip()
            scenario = scenario_of(txn_id)
            if scenarios is not None and scenario not in scenarios:
                continue

            amount, amount_defects = _parse_amount(row.get("amount", ""))
            day, day_defects = _parse_day(row.get("date", ""))
            entries.append(
                LedgerEntry(
                    txn_id=txn_id,
                    scenario_id=scenario,
                    day=day or date(1970, 1, 1),
                    account_id=(row.get("account_id") or "").strip(),
                    counterparty=(row.get("counterparty") or "").strip(),
                    description=(row.get("description") or "").strip(),
                    amount=amount,
                    currency=(row.get("currency") or "").strip().upper(),
                    defects=amount_defects + day_defects,
                )
            )
    return entries


def by_scenario(entries: list[LedgerEntry]) -> dict[str, list[LedgerEntry]]:
    grouped: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.scenario_id, []).append(entry)
    return grouped
