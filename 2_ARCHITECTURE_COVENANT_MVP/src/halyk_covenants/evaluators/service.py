from datetime import date

import duckdb

from halyk_covenants.domain import CovenantResult, CovenantSpec, FailureStage
from halyk_covenants.evaluators.registry import EvaluatorRegistry
from halyk_covenants.observability import annotate_current_trace, trace_context, trace_stage
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

    @trace_stage(
        "evaluation.pair",
        run_type="chain",
        tags=("evaluation", "deterministic"),
        failure_stage=FailureStage.CALCULATION,
    )
    def evaluate(
        self,
        covenant: CovenantSpec,
        borrower_id: str,
        evaluation_date: date | None = None,
    ) -> CovenantResult:
        metadata = {
            "borrower_id": borrower_id,
            "covenant_id": covenant.covenant_id,
            "covenant_group_id": covenant.covenant_group_id,
            "metric_type": covenant.metric.metric_type,
            "evidence_mode": covenant.evidence_mode.value,
            "evaluation_date": evaluation_date.isoformat() if evaluation_date else None,
            "covenant_status": covenant.status,
        }
        annotate_current_trace(metadata={key: value for key, value in metadata.items() if value})
        with trace_context(**metadata):
            if covenant.status != "compiled":
                return self._failed(
                    covenant,
                    borrower_id,
                    FailureStage.COMPILATION,
                    f"covenant status {covenant.status!r} is not executable",
                )
            try:
                evaluator = self.registry.get(covenant.metric.metric_type)
                return evaluator.evaluate(covenant, borrower_id, self.db, evaluation_date)
            except duckdb.Error as exc:
                return self._failed(covenant, borrower_id, FailureStage.QUERY, str(exc), exc)
            except Exception as exc:
                return self._failed(covenant, borrower_id, FailureStage.CALCULATION, str(exc), exc)

    @staticmethod
    def _failed(
        covenant: CovenantSpec,
        borrower_id: str,
        stage: FailureStage,
        message: str,
        exc: Exception | None = None,
    ) -> CovenantResult:
        metadata = {"failure_stage": stage.value}
        if exc is not None:
            metadata["error_type"] = type(exc).__name__
        annotate_current_trace(metadata=metadata, tags=("failed", stage.value))
        return CovenantResult(
            borrower_id=borrower_id,
            covenant_id=covenant.covenant_group_id or covenant.covenant_id,
            verdict="unknown",
            number=None,
            status="failed",
            failure_stage=stage,
            errors=[message],
        )
