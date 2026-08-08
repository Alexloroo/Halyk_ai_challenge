from __future__ import annotations

import asyncio
from dataclasses import dataclass

from halyk.categorize import Category
from halyk.llm_categorize import (
    CategoryRequest,
    FlowDirection,
    TransactionCategorySpec,
    _category_concurrency,
    resolve_categories,
)


def _resolution(
    category: Category = Category.OPEX,
    direction: FlowDirection = FlowDirection.OUTFLOW,
) -> TransactionCategorySpec:
    return TransactionCategorySpec(
        category=category,
        direction=direction,
        transaction_nature="general_service",
        matched_terms=["service"],
    )


@dataclass
class FakeStructured:
    failures: frozenset[str] = frozenset()
    active: int = 0
    max_active: int = 0
    calls: int = 0

    async def ainvoke(self, messages):
        description = messages[-1]["content"]
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls += 1
        try:
            await asyncio.sleep(0.01)
            if any(value in description for value in self.failures):
                raise RuntimeError("classification failed")
            return _resolution()
        finally:
            self.active -= 1


class FakeLLM:
    def __init__(self, structured) -> None:
        self.structured = structured
        self.builds = 0

    def with_structured_output(self, schema):
        assert schema is TransactionCategorySpec
        self.builds += 1
        return self.structured


def _request(index: int, description: str | None = None) -> CategoryRequest:
    return CategoryRequest(
        key=f"TXN-T-{index}",
        description=description or f"novel service {index}",
        counterparty="Vendor LLP",
        direction=FlowDirection.OUTFLOW,
    )


def test_default_category_concurrency_is_fifty(monkeypatch) -> None:
    monkeypatch.delenv("HALYK_CATEGORY_LLM_CONCURRENCY", raising=False)

    assert _category_concurrency() == 50


def test_category_requests_are_bounded_deduplicated_and_share_one_runnable(
    monkeypatch,
) -> None:
    structured = FakeStructured()
    llm = FakeLLM(structured)
    monkeypatch.setenv("HALYK_CATEGORY_LLM_CONCURRENCY", "2")
    monkeypatch.setattr("halyk.llm_extract._build_llm", lambda: llm)
    requests = [_request(index) for index in range(6)]
    requests.append(_request(99, description="novel service 1"))

    results = resolve_categories(requests)

    assert len(results) == 7
    assert structured.calls == 6
    assert structured.max_active == 2
    assert llm.builds == 1


def test_one_category_failure_does_not_cancel_siblings(monkeypatch) -> None:
    structured = FakeStructured(failures=frozenset({"bad service"}))
    llm = FakeLLM(structured)
    monkeypatch.setattr("halyk.llm_extract._build_llm", lambda: llm)

    async def no_wait(delay: float) -> None:
        return None

    monkeypatch.setattr("halyk.llm_categorize.asyncio.sleep", no_wait)
    requests = [_request(1, "good service"), _request(2, "bad service")]

    results = resolve_categories(requests)

    assert results[requests[0].key].resolution is not None
    assert results[requests[1].key].resolution is None
    assert results[requests[1].key].attempts == 3


def test_direction_conflict_is_retried_before_acceptance(monkeypatch) -> None:
    class SequencedStructured:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return _resolution(Category.MARKETING, FlowDirection.OUTFLOW)
            return TransactionCategorySpec(
                category=Category.FINANCING,
                direction=FlowDirection.INFLOW,
                transaction_nature="loan_drawdown",
                matched_terms=["facility proceeds"],
            )

    structured = SequencedStructured()
    monkeypatch.setattr(
        "halyk.llm_extract._build_llm", lambda: FakeLLM(structured)
    )

    async def no_wait(delay: float) -> None:
        return None

    monkeypatch.setattr("halyk.llm_categorize.asyncio.sleep", no_wait)
    request = CategoryRequest(
        key="TXN-T-1",
        description="new facility proceeds wording",
        counterparty="Bank",
        direction=FlowDirection.INFLOW,
    )

    result = resolve_categories([request])[request.key]

    assert structured.calls == 2
    assert result.resolution is not None
    assert result.resolution.category is Category.FINANCING
