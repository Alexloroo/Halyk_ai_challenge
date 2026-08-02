from __future__ import annotations

from datetime import date

from halyk_covenants.domain import CovenantSpec
from halyk_covenants.observability import trace_stage


class CovenantNotEffective(LookupError):
    pass


class OverlappingCovenantVersions(RuntimeError):
    pass


class TemporalResolver:
    def __init__(self, covenants: list[CovenantSpec]) -> None:
        self.covenants = covenants

    @trace_stage("covenant.temporal.resolve", run_type="retriever", tags=("evaluation",))
    def resolve(self, group_id: str, borrower_id: str, at_date: date) -> CovenantSpec:
        active = [
            covenant
            for covenant in self.covenants
            if (covenant.covenant_group_id or covenant.covenant_id) == group_id
            and borrower_id in covenant.borrower_ids
            and (covenant.effective_from is None or covenant.effective_from <= at_date)
            and (covenant.effective_to is None or at_date <= covenant.effective_to)
        ]
        if not active:
            raise CovenantNotEffective(
                f"No covenant version for group={group_id}, borrower={borrower_id}, date={at_date}"
            )
        if len(active) > 1:
            ids = ", ".join(sorted(item.covenant_id for item in active))
            raise OverlappingCovenantVersions(
                f"Overlapping covenant versions for group={group_id} at {at_date}: {ids}"
            )
        return active[0]
