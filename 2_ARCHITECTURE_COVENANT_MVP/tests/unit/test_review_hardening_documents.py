from __future__ import annotations

from decimal import Decimal

from halyk_covenants.covenants.compiler import apply_resolved_candidate_facts
from halyk_covenants.covenants.detector import CovenantCandidate, CovenantDetector
from halyk_covenants.domain import (
    Borrower,
    ConditionSpec,
    CovenantSpec,
    DocumentBlock,
    MetricSpec,
    SourceRef,
)
from halyk_covenants.pipeline.preprocess import PreprocessPipeline
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.vlm.paddle_layout import PaddleLayoutProvider


def _block(
    block_id: str,
    text: str,
    *,
    page: int = 1,
    block_type: str = "text",
    bbox: tuple[float, float, float, float] = (0, 0, 100, 20),
    borrower_ids: list[str] | None = None,
) -> DocumentBlock:
    return DocumentBlock(
        block_id=block_id,
        document_id="doc-1",
        page=page,
        block_type=block_type,
        text=text,
        bbox=bbox,
        borrower_ids=borrower_ids or [],
        extraction_method="native",
        confidence=Decimal("1"),
        source=SourceRef(document_id="doc-1", page=page, bbox=bbox),
    )


def _spec(*, borrower_ids: list[str], covenant_id: str = "invented-by-model") -> CovenantSpec:
    return CovenantSpec(
        covenant_id=covenant_id,
        raw_text="placeholder",
        borrower_ids=borrower_ids,
        metric=MetricSpec(metric_type="max", field="amount"),
        condition=ConditionSpec(comparator="<=", threshold=5_000_000, currency="KZT"),
        source=SourceRef(document_id="model", page=99),
        confidence=1,
    )


def test_detector_assembles_adjacent_text_blocks_before_signal_detection() -> None:
    blocks = [
        _block("b1", "The Borrower must not exceed", bbox=(0, 0, 100, 20)),
        _block("b2", "5000000 KZT in outgoing payments per month.", bbox=(0, 22, 100, 42)),
    ]

    candidates = CovenantDetector().detect(blocks)

    assert len(candidates) == 1
    assert "must not exceed" in candidates[0].raw_text
    assert "5000000" in candidates[0].raw_text


def test_compiler_overlay_preserves_model_subset_inside_resolved_multi_borrower_scope() -> None:
    candidate = CovenantCandidate(
        candidate_id="candidate-1",
        raw_text="Maximum payment must not exceed 5000000 KZT",
        ordinal=1,
        borrower_ids=["B001", "B002"],
        source=SourceRef(document_id="doc-1", page=1),
        confidence=1,
    )

    resolved = apply_resolved_candidate_facts(_spec(borrower_ids=["B001"]), candidate)

    assert resolved.borrower_ids == ["B001"]
    assert resolved.covenant_id != "invented-by-model"


def test_explicit_covenant_code_remains_stable_deterministic_identity() -> None:
    candidate = CovenantCandidate(
        candidate_id="candidate-2",
        raw_text="COV-LIMIT-7 maximum payment must not exceed 5000000 KZT",
        ordinal=1,
        borrower_ids=["B001"],
        source=SourceRef(document_id="doc-1", page=1),
        confidence=1,
    )

    resolved = apply_resolved_candidate_facts(_spec(borrower_ids=["B001"]), candidate)

    assert resolved.covenant_id == "COV-LIMIT-7"


def test_borrower_scope_does_not_leak_to_next_page_without_new_evidence() -> None:
    store = DuckDBStore()
    store.save_borrowers([Borrower(borrower_id="B001", canonical_name="Alpha LLP")])
    pipeline = PreprocessPipeline(store)
    blocks = [
        _block("p1", "Borrower: Alpha LLP", page=1),
        _block("p2", "Unrelated appendix text", page=2),
    ]

    scoped = pipeline._annotate_borrower_scopes(blocks)

    assert scoped[0].borrower_ids == ["B001"]
    assert scoped[1].borrower_ids == []
    store.close()


def test_layout_provider_caches_pipeline_and_assigns_table_coordinates() -> None:
    calls: list[str] = []

    class FakePipeline:
        def predict(self, image: bytes):  # type: ignore[no-untyped-def]
            del image
            return {
                "res": {
                    "rec_texts": ["Borrower", "Limit", "B001", "5000000"],
                    "rec_scores": [1, 1, 1, 1],
                    "rec_polys": [
                        [(0, 0), (40, 0), (40, 10), (0, 10)],
                        [(60, 0), (100, 0), (100, 10), (60, 10)],
                        [(0, 20), (40, 20), (40, 30), (0, 30)],
                        [(60, 20), (100, 20), (100, 30), (60, 30)],
                    ],
                }
            }

    def factory(device: str) -> FakePipeline:
        calls.append(device)
        return FakePipeline()

    provider = PaddleLayoutProvider(pipeline_factory=factory, preferred_device="cpu")
    first = provider.extract(b"png", document_id="doc", page=1)
    second = provider.extract(b"png", document_id="doc", page=2)

    assert calls == ["cpu"]
    assert [(block.row_index, block.column_index) for block in first] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    ]
    assert len({block.table_id for block in first}) == 1
    assert all(block.table_id for block in first + second)
