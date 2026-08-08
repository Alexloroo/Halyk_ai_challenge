from __future__ import annotations

import asyncio
from dataclasses import dataclass

from halyk.docs import DocKind, Edition
from halyk.llm_documents import (
    DocumentClassificationRequest,
    DocumentClassificationSpec,
    _document_concurrency,
    resolve_document_classifications,
)


def _resolution(kind: DocKind = DocKind.CREDIT_AGREEMENT):
    return DocumentClassificationSpec(
        kind=kind,
        edition=Edition.CURRENT,
        matched_terms=["CREDIT AGREEMENT"] if kind is not DocKind.UNKNOWN else [],
    )


@dataclass
class FakeStructured:
    failures: frozenset[str] = frozenset()
    active: int = 0
    max_active: int = 0

    async def ainvoke(self, messages):
        payload = messages[-1]["content"]
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            if any(value in payload for value in self.failures):
                raise RuntimeError("document classification failed")
            return _resolution()
        finally:
            self.active -= 1


class FakeLLM:
    def __init__(self, structured) -> None:
        self.structured = structured
        self.builds = 0

    def with_structured_output(self, schema):
        assert schema is DocumentClassificationSpec
        self.builds += 1
        return self.structured


def _request(index: int, marker: str = "") -> DocumentClassificationRequest:
    return DocumentClassificationRequest(
        key=f"document-{index}.pdf",
        text=f"CREDIT AGREEMENT {marker}\nArticle 6 Financial Covenants\n6.1 ratio",
    )


def test_default_document_concurrency_is_twenty(monkeypatch) -> None:
    monkeypatch.delenv("HALYK_DOCUMENT_LLM_CONCURRENCY", raising=False)

    assert _document_concurrency() == 20


def test_document_requests_are_bounded_and_share_one_runnable(monkeypatch) -> None:
    structured = FakeStructured()
    llm = FakeLLM(structured)
    monkeypatch.setenv("HALYK_DOCUMENT_LLM_CONCURRENCY", "2")
    monkeypatch.setattr("halyk.llm_extract._build_llm", lambda: llm)

    results = resolve_document_classifications([_request(index) for index in range(6)])

    assert len(results) == 6
    assert structured.max_active == 2
    assert llm.builds == 1
    assert all(result.resolution is not None for result in results.values())


def test_one_document_failure_does_not_cancel_siblings(monkeypatch) -> None:
    structured = FakeStructured(failures=frozenset({"broken-marker"}))
    monkeypatch.setattr(
        "halyk.llm_extract._build_llm", lambda: FakeLLM(structured)
    )

    async def no_wait(delay: float) -> None:
        return None

    monkeypatch.setattr("halyk.llm_documents.asyncio.sleep", no_wait)
    requests = [_request(1), _request(2, "broken-marker")]

    results = resolve_document_classifications(requests)

    assert results[requests[0].key].resolution is not None
    assert results[requests[1].key].resolution is None
    assert results[requests[1].key].attempts == 3


def test_non_binding_training_memo_cannot_be_promoted_to_agreement(monkeypatch) -> None:
    class SequencedStructured:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return _resolution()
            return _resolution(DocKind.UNKNOWN)

    structured = SequencedStructured()
    monkeypatch.setattr(
        "halyk.llm_extract._build_llm", lambda: FakeLLM(structured)
    )

    async def no_wait(delay: float) -> None:
        return None

    monkeypatch.setattr("halyk.llm_documents.asyncio.sleep", no_wait)
    request = DocumentClassificationRequest(
        key="training.pdf",
        text=(
            "CREDIT AGREEMENT training example. This document is not a credit agreement "
            "and creates no obligations."
        ),
    )

    result = resolve_document_classifications([request])[request.key]

    assert structured.calls == 2
    assert result.resolution is not None
    assert result.resolution.kind is DocKind.UNKNOWN
