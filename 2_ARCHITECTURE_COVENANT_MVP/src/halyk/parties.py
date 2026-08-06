"""Related parties, from the KYC dossier.

The dossier lists counterparties with their voting stake and then states the
threshold in prose — "20.0% and above are treated as related parties for the
purposes of the Agreement". Both halves matter: for ACC-7801 the table holds
34.5%, 18.7% and 6.2%, and only the first qualifies. Taking every row from the
table would triple the related-party total.

The threshold is read from the document. It is never defaulted: assuming 20%
because one dossier said so is exactly the kind of guess this rewrite exists to
remove.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .ledger import LedgerEntry

THRESHOLD = re.compile(
    r"владеет\s+(\d+(?:[.,]\d+)?)\s*%\s*и\s*более|"
    r"(\d+(?:[.,]\d+)?)\s*%\s*и\s*более",
    re.I,
)
#: The dossier renders its ownership table one cell per line:
#:     Aktau Holdings LLP
#:     34.5%
#: so the name and its share are on separate lines, not side by side.
HOLDING = re.compile(
    r"^[ \t]*([^\n%]*?(?:LLP|JSC|LLC|Ltd|АО|ТОО|ООО))[ \t]*\n"
    r"[ \t]*(\d+(?:[.,]\d+)?)[ \t]*%",
    re.MULTILINE,
)


@dataclass
class RelatedParties:
    scenario_id: str
    threshold_percent: Decimal | None
    holdings: list[tuple[str, Decimal]] = field(default_factory=list)
    names: frozenset[str] = frozenset()

    @property
    def resolved(self) -> bool:
        return self.threshold_percent is not None


def _number(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def extract_related_parties(scenario_id: str, kyc_text: str) -> RelatedParties:
    match = THRESHOLD.search(kyc_text)
    threshold = _number(match.group(1) or match.group(2)) if match else None

    holdings = [
        (" ".join(name.split()), _number(share))
        for name, share in HOLDING.findall(kyc_text)
    ]
    names = (
        frozenset(name for name, share in holdings if share >= threshold)
        if threshold is not None
        else frozenset()
    )
    return RelatedParties(
        scenario_id=scenario_id,
        threshold_percent=threshold,
        holdings=holdings,
        names=names,
    )


def _key(name: str) -> str:
    """Loose form for comparison: case and legal suffix carry no meaning here."""
    text = re.sub(r"\b(LLP|JSC|LLC|Ltd|АО|ТОО|ООО)\b", "", name, flags=re.I)
    return re.sub(r"[^a-zа-я0-9 ]", "", text.casefold()).strip()


def mark_related(entries: list[LedgerEntry], parties: RelatedParties) -> int:
    """Flag entries paid to a related party. Returns how many were flagged."""
    if not parties.names:
        return 0
    keys = {_key(name) for name in parties.names}
    flagged = 0
    for entry in entries:
        counterparty = _key(entry.counterparty)
        if any(key and (key in counterparty or counterparty in key) for key in keys):
            entry.is_related_party = True
            flagged += 1
    return flagged
