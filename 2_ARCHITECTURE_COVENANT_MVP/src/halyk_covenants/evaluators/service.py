from datetime import date

from halyk_covenants.domain import CovenantResult, CovenantSpec
from halyk_covenants.evaluators.registry import EvaluatorRegistry
from halyk_covenants.observability import trace_stage
from halyk_covenants.storage import DuckDBStore


class EvaluationService:
    """Fault-isolating entry point for one borrower/covenant pair."""

    def __init__(
        self,
        db: DuckDBStore,
        registry: EvaluatorRegistry | None = None,
    ) -> None:
        self.db = db
        self.registry = registry or EvaluatorRegistry()

    @trace_stage("evaluation.pair", run_type="chain", tags=("evaluation", "deterministic"))
    def evaluate(
        self,
        covenant: CovenantSpec,
        borrower_id: str,
        evaluation_date: date | None = None,
    ) -> CovenantResult:
        try:
            evaluator = self.registry.get(covenant.metric.metric_type)
            return evaluator.evaluate(covenant, borrower_id, self.db, evaluation_date)
        except Exception as exc:
            return CovenantResult(
                borrower_id=borrower_id,
                covenant_id=covenant.covenant_id,
                verdict="unknown",
                number=None,
                status="failed",
                errors=[str(exc)],
            )
