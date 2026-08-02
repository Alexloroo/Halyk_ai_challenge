from decimal import Decimal

from halyk_covenants.covenants import (
    CompilationOutcome,
    CompilerGraph,
    CovenantCandidate,
)
from halyk_covenants.domain import ConditionSpec, CovenantSpec, MetricSpec, SourceRef


def candidate() -> CovenantCandidate:
    return CovenantCandidate(
        candidate_id="candidate-1",
        raw_text="Outgoing sum <= 10 KZT",
        ordinal=1,
        borrower_ids=["B1"],
        source=SourceRef(document_id="d1", page=1),
        confidence=Decimal("0.9"),
    )


def spec() -> CovenantSpec:
    return CovenantSpec(
        covenant_id="C1",
        raw_text=candidate().raw_text,
        borrower_ids=["B1"],
        metric=MetricSpec(metric_type="sum", field="amount"),
        condition=ConditionSpec(comparator="<=", threshold=10, currency="KZT"),
        source=candidate().source,
        confidence=0.9,
    )


class Compiler:
    def __init__(self, outcome: CompilationOutcome) -> None:
        self.outcome = outcome

    def compile(self, candidate: CovenantCandidate, context: str) -> CompilationOutcome:
        return self.outcome


class FailIfCalledRepairer:
    def repair(self, **kwargs: object) -> CompilationOutcome:
        raise AssertionError(f"repair must not be called: {kwargs}")


class ValidRepairer:
    def __init__(self) -> None:
        self.calls = 0

    def repair(self, **kwargs: object) -> CompilationOutcome:
        self.calls += 1
        return CompilationOutcome(route="straightforward", specs=[spec()])


class AlwaysInvalidRepairer:
    def __init__(self) -> None:
        self.calls = 0

    def repair(self, **kwargs: object) -> CompilationOutcome:
        self.calls += 1
        return CompilationOutcome(route="ambiguous", validation_errors=["still invalid"])


def test_straightforward_spec_never_calls_repair_model() -> None:
    graph = CompilerGraph(
        compiler=Compiler(CompilationOutcome(route="straightforward", specs=[spec()])),
        repairer=FailIfCalledRepairer(),
    )

    final = graph.invoke({"candidate": candidate(), "context": "", "attempt": 0})

    assert final["status"] == "compiled"
    assert final["attempt"] == 0


def test_one_valid_repair_finishes_graph() -> None:
    repairer = ValidRepairer()
    graph = CompilerGraph(
        compiler=Compiler(CompilationOutcome(route="ambiguous", validation_errors=["currency"])),
        repairer=repairer,
    )

    final = graph.invoke({"candidate": candidate(), "context": "", "attempt": 0})

    assert final["status"] == "compiled"
    assert final["attempt"] == 1
    assert repairer.calls == 1


def test_invalid_repairs_stop_after_three_attempts() -> None:
    repairer = AlwaysInvalidRepairer()
    graph = CompilerGraph(
        compiler=Compiler(CompilationOutcome(route="ambiguous", validation_errors=["bad"])),
        repairer=repairer,
        max_attempts=3,
    )

    final = graph.invoke({"candidate": candidate(), "context": "", "attempt": 0})

    assert final["status"] == "failed_compilation"
    assert final["attempt"] == 3
    assert repairer.calls == 3
