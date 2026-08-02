from __future__ import annotations

from halyk_covenants.domain import FailureStage
from halyk_covenants.observability import current_trace_metadata, trace_context


def test_trace_context_merges_nested_metadata_and_restores_parent() -> None:
    assert current_trace_metadata() == {}

    with trace_context(run_id="RUN-1", dataset="synthetic-v2"):
        assert current_trace_metadata() == {
            "run_id": "RUN-1",
            "dataset": "synthetic-v2",
        }
        with trace_context(borrower_id="B001", covenant_id="COV-A1"):
            assert current_trace_metadata() == {
                "run_id": "RUN-1",
                "dataset": "synthetic-v2",
                "borrower_id": "B001",
                "covenant_id": "COV-A1",
            }
        assert current_trace_metadata() == {
            "run_id": "RUN-1",
            "dataset": "synthetic-v2",
        }

    assert current_trace_metadata() == {}


def test_failure_stage_values_are_stable_for_langsmith_filters() -> None:
    assert FailureStage.OCR.value == "ocr"
    assert FailureStage.COMPILATION.value == "compilation"
    assert FailureStage.CALCULATION.value == "calculation"
    assert FailureStage.EVIDENCE.value == "evidence"
    assert FailureStage.SERIALIZATION.value == "serialization"
