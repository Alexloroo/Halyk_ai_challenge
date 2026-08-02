from datetime import date

import pytest

from halyk_covenants.covenants import (
    CovenantNotEffective,
    OverlappingCovenantVersions,
    TemporalResolver,
)
from halyk_covenants.domain import ConditionSpec, CovenantSpec, MetricSpec, SourceRef


def version(covenant_id: str, threshold: int, start: date, end: date | None) -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        covenant_group_id="LIMIT",
        raw_text="Monthly limit",
        borrower_ids=["B001"],
        metric=MetricSpec(metric_type="sum", field="amount"),
        condition=ConditionSpec(comparator="<=", threshold=threshold, currency="KZT"),
        effective_from=start,
        effective_to=end,
        source=SourceRef(document_id="contract", page=1),
        confidence=1,
    )


def test_april_and_june_resolve_different_versions() -> None:
    resolver = TemporalResolver(
        [
            version("V1", 10_000_000, date(2026, 1, 1), date(2026, 4, 30)),
            version("V2", 15_000_000, date(2026, 5, 1), None),
        ]
    )

    assert resolver.resolve("LIMIT", "B001", date(2026, 4, 30)).condition.threshold == 10_000_000
    assert resolver.resolve("LIMIT", "B001", date(2026, 6, 30)).condition.threshold == 15_000_000


def test_overlapping_versions_fail_explicitly() -> None:
    resolver = TemporalResolver(
        [
            version("V1", 10, date(2026, 1, 1), date(2026, 5, 31)),
            version("V2", 15, date(2026, 5, 1), None),
        ]
    )

    with pytest.raises(OverlappingCovenantVersions):
        resolver.resolve("LIMIT", "B001", date(2026, 5, 1))


def test_missing_effective_version_fails_explicitly() -> None:
    resolver = TemporalResolver([version("V1", 10, date(2026, 5, 1), None)])

    with pytest.raises(CovenantNotEffective):
        resolver.resolve("LIMIT", "B001", date(2026, 4, 30))
