from halyk_covenants.domain import (
    ConditionSpec,
    CovenantResult,
    CovenantSpec,
    MetricSpec,
    SourceRef,
)
from halyk_covenants.verification import RepairGraph


def spec() -> CovenantSpec:
    return CovenantSpec(
        covenant_id="C1",
        raw_text="Count limit",
        borrower_ids=["B1"],
        metric=MetricSpec(metric_type="count", field="transaction_id"),
        condition=ConditionSpec(comparator="<=", threshold=2),
        source=SourceRef(document_id="d1", page=1),
        confidence=1,
    )


def initial_state() -> dict[str, object]:
    return {
        "spec": spec(),
        "result": CovenantResult(
            borrower_id="B1",
            covenant_id="C1",
            verdict="violated",
            number=3,
            status="partial",
            errors=["evidence missing"],
        ),
        "transaction_snapshot_hash": "unchanged",
        "attempt": 0,
    }


class FakeProposer:
    def __init__(self, patch: dict[str, object]) -> None:
        self.patch = patch
        self.calls = 0

    def propose(self, state: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        return self.patch


def test_repair_patch_cannot_write_number_or_transactions() -> None:
    graph = RepairGraph(proposer=FakeProposer({"number": 1, "transactions": []}))

    final = graph.invoke(initial_state())

    assert final["status"] == "rejected_unauthorized_patch"
    assert final["transaction_snapshot_hash"] == "unchanged"


def test_evidence_strategy_is_an_authorized_spec_only_patch() -> None:
    graph = RepairGraph(
        proposer=FakeProposer({"evidence_strategy": {"evidence_mode": "trigger_transaction"}})
    )

    final = graph.invoke(initial_state())

    assert final["status"] == "patched"
    assert final["spec"].evidence_mode.value == "trigger_transaction"
    assert final["result"].number == 3
    assert final["result"].verdict == "violated"
    assert final["transaction_snapshot_hash"] == "unchanged"


def test_invalid_allowed_patch_stops_after_two_attempts() -> None:
    proposer = FakeProposer({"period_mapping": {"date_field": "invented_date"}})
    graph = RepairGraph(proposer=proposer, max_attempts=2)

    final = graph.invoke(initial_state())

    assert final["status"] == "repair_exhausted"
    assert final["attempt"] == 2
    assert proposer.calls == 2
