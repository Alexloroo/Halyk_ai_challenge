from types import SimpleNamespace

from halyk_covenants.evals.langsmith import (
    evidence_exact_evaluator,
    number_exact_evaluator,
    verdict_exact_evaluator,
)


def _run(outputs: dict):
    return SimpleNamespace(outputs=outputs)


def _example(outputs: dict):
    return SimpleNamespace(outputs=outputs)


def test_result_evaluators_use_reference_outputs_without_network() -> None:
    expected = {
        "borrower_id": "B001",
        "covenant_id": "COV-A1",
        "verdict": "violated",
        "number": "16000000",
        "evidence_transaction_id": None,
        "status": "success",
    }
    actual = {**expected, "verdict": "complied"}

    assert number_exact_evaluator(_run(actual), _example(expected)) == {
        "key": "number_exact",
        "score": 1,
    }
    assert verdict_exact_evaluator(_run(actual), _example(expected)) == {
        "key": "verdict_exact",
        "score": 0,
    }
    assert evidence_exact_evaluator(_run(actual), _example(expected)) == {
        "key": "evidence_exact",
        "score": 1,
    }


def test_result_evaluators_accept_result_nested_payload() -> None:
    expected = {
        "borrower_id": "B001",
        "covenant_id": "COV-A1",
        "verdict": "violated",
        "number": 6000000,
        "evidence_transaction_id": "TX-A2",
        "status": "success",
    }
    actual = {"result": expected}

    assert evidence_exact_evaluator(_run(actual), _example(expected))["score"] == 1
