from __future__ import annotations

from typing import Any

from halyk_covenants.domain import CovenantResult
from halyk_covenants.evals.scoring import score_covenant_result


def _outputs(value: Any) -> dict[str, Any]:
    payload = getattr(value, "outputs", None)
    if payload is None and isinstance(value, dict):
        payload = value
    if not isinstance(payload, dict):
        raise ValueError("LangSmith run/example must expose mapping outputs")
    nested = payload.get("result")
    if isinstance(nested, dict):
        return nested
    return payload


def _score(run: Any, example: Any) -> dict[str, int]:
    expected = CovenantResult.model_validate(_outputs(example))
    actual = CovenantResult.model_validate(_outputs(run))
    return score_covenant_result(expected, actual)


def number_exact_evaluator(run: Any, example: Any) -> dict[str, str | int]:
    return {"key": "number_exact", "score": _score(run, example)["number_exact"]}


def verdict_exact_evaluator(run: Any, example: Any) -> dict[str, str | int]:
    return {"key": "verdict_exact", "score": _score(run, example)["verdict_exact"]}


def evidence_exact_evaluator(run: Any, example: Any) -> dict[str, str | int]:
    return {"key": "evidence_exact", "score": _score(run, example)["evidence_exact"]}


def full_exact_match_evaluator(run: Any, example: Any) -> dict[str, str | int]:
    return {"key": "full_exact_match", "score": _score(run, example)["full_exact_match"]}


def covenant_result_evaluators() -> list[Any]:
    """Return code evaluators suitable for ``langsmith.evaluate``.

    The functions themselves are network-free. Only the caller that invokes LangSmith's remote
    experiment API needs credentials/network access.
    """
    return [
        number_exact_evaluator,
        verdict_exact_evaluator,
        evidence_exact_evaluator,
        full_exact_match_evaluator,
    ]
