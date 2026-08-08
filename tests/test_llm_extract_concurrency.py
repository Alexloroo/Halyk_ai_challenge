from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

from halyk.llm_extract import AggKind, FormulaSpec, OutputKind, _llm_concurrency, extract_formulas
from halyk.rules import Rule, RuleKind


def _rule(text: str, *, kind: RuleKind = RuleKind.RATIO) -> Rule:
    return Rule(
        scenario_id="T",
        clause="6.1",
        heading=text,
        text=text,
        kind=kind,
        comparator="<=",
        threshold=Decimal("1"),
        period=None,
    )


def _spec() -> FormulaSpec:
    return FormulaSpec(
        output_kind=OutputKind.RATIO,
        numerator_agg=AggKind.SUM_INFLOW,
        numerator_categories=[],
        denominator_agg=AggKind.REVENUE,
        denominator_categories=[],
        comparator="<=",
    )


@dataclass
class FakeStructured:
    failures: frozenset[str] = frozenset()
    active: int = 0
    max_active: int = 0

    async def ainvoke(self, messages):
        text = messages[-1]["content"]
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            if text in self.failures:
                raise RuntimeError(f"failed: {text}")
            return _spec()
        finally:
            self.active -= 1


class FakeLLM:
    def __init__(self, structured: FakeStructured) -> None:
        self.structured = structured
        self.structured_builds = 0
        self.root_async_client = FakeAsyncClient()
        self.root_client = FakeSyncClient()

    def with_structured_output(self, schema):
        assert schema is FormulaSpec
        self.structured_builds += 1
        return self.structured


class FakeAsyncClient:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class FakeSyncClient:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def test_default_concurrency_is_fifty(monkeypatch) -> None:
    monkeypatch.delenv("HALYK_LLM_CONCURRENCY", raising=False)

    assert _llm_concurrency() == 50


def test_extract_formulas_bounds_concurrency_and_builds_runnable_once(
    monkeypatch,
) -> None:
    structured = FakeStructured()
    llm = FakeLLM(structured)
    monkeypatch.setenv("HALYK_LLM_CONCURRENCY", "2")
    monkeypatch.setattr("halyk.llm_extract._build_llm", lambda: llm)
    rules = {"T": {f"6.{index}": _rule(f"clause-{index}") for index in range(1, 7)}}

    formulas = extract_formulas(rules)

    assert len(formulas) == 6
    assert structured.max_active == 2
    assert llm.structured_builds == 1
    assert llm.root_async_client.close_calls == 1
    assert llm.root_client.close_calls == 1


def test_failed_request_does_not_cancel_siblings(monkeypatch) -> None:
    structured = FakeStructured(failures=frozenset({"bad"}))
    llm = FakeLLM(structured)
    monkeypatch.setattr("halyk.llm_extract._build_llm", lambda: llm)

    backoffs: list[float] = []

    async def no_wait(delay: float) -> None:
        if delay >= 1:
            backoffs.append(delay)
        return None

    monkeypatch.setattr("halyk.llm_extract.asyncio.sleep", no_wait)
    rules = {"T": {"6.1": _rule("good-1"), "6.2": _rule("bad"), "6.3": _rule("good-2")}}

    formulas = extract_formulas(rules)

    assert set(formulas) == {"T/6.1", "T/6.3"}
    assert backoffs == [1, 2, 4]


def test_successful_async_result_still_receives_fixup(monkeypatch) -> None:
    structured = FakeStructured()
    llm = FakeLLM(structured)
    monkeypatch.setattr("halyk.llm_extract._build_llm", lambda: llm)
    rules = {
        "T": {
            "6.1": _rule("суммы выручки и поступлений по финансированию"),
            "6.2": _rule("simple", kind=RuleKind.MAX_CATEGORY_SPEND),
        }
    }

    formulas = extract_formulas(rules)

    assert set(formulas) == {"T/6.1"}
    assert formulas["T/6.1"].numerator_agg is AggKind.REVENUE_PLUS_FINANCING


def test_semantically_invalid_formula_is_retried(monkeypatch) -> None:
    valid = _spec()
    invalid = FormulaSpec(
        output_kind=OutputKind.DOLLAR_AMOUNT,
        numerator_agg=AggKind.SUM_INFLOW,
        numerator_categories=[],
        comparator="<=",
    )

    class SequencedStructured:
        def __init__(self) -> None:
            self.results = [invalid, valid]
            self.calls = 0

        async def ainvoke(self, messages):
            result = self.results[self.calls]
            self.calls += 1
            return result

    structured = SequencedStructured()
    llm = FakeLLM(structured)
    monkeypatch.setattr("halyk.llm_extract._build_llm", lambda: llm)

    async def no_wait(delay: float) -> None:
        return None

    monkeypatch.setattr("halyk.llm_extract.asyncio.sleep", no_wait)
    formulas = extract_formulas({"T": {"6.1": _rule("ratio must not exceed 1x")}})

    assert structured.calls == 2
    assert formulas["T/6.1"].output_kind is OutputKind.RATIO
