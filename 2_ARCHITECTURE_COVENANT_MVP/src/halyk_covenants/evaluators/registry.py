from collections.abc import Mapping

from halyk_covenants.evaluators.aggregate import (
    AverageEvaluator,
    CountEvaluator,
    MaxEvaluator,
    MinEvaluator,
    SumEvaluator,
)
from halyk_covenants.evaluators.base import CovenantEvaluator


class EvaluatorRegistry:
    def __init__(self, evaluators: Mapping[str, CovenantEvaluator] | None = None) -> None:
        defaults: dict[str, CovenantEvaluator] = {
            "sum": SumEvaluator(),
            "count": CountEvaluator(),
            "max": MaxEvaluator(),
            "min": MinEvaluator(),
            "avg": AverageEvaluator(),
        }
        self._evaluators = dict(evaluators) if evaluators is not None else defaults

    def get(self, metric_type: str) -> CovenantEvaluator:
        try:
            return self._evaluators[metric_type]
        except KeyError as exc:
            raise ValueError(f"unsupported metric type: {metric_type}") from exc

    def register(self, metric_type: str, evaluator: CovenantEvaluator) -> None:
        self._evaluators[metric_type] = evaluator
