from __future__ import annotations

from datetime import date
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.covenants import CovenantRegistry
from halyk_covenants.domain import CovenantResult
from halyk_covenants.evaluators import EvaluationService, TemporalEvaluationService
from halyk_covenants.observability import annotate_current_trace, trace_context, trace_stage
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.verification import ResultVerifier, VerificationReport


class BatchEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    evaluation_date: date
    expected_pair_count: int = Field(ge=0)
    actual_pair_count: int = Field(ge=0)
    results: list[CovenantResult]
    verification: VerificationReport


class BatchEvaluationPipeline:
    def __init__(
        self,
        store: DuckDBStore,
        registry: CovenantRegistry | None = None,
        *,
        service: EvaluationService | None = None,
        verifier: ResultVerifier | None = None,
    ) -> None:
        self.store = store
        self.registry = registry or CovenantRegistry(store)
        self.service = service or EvaluationService(store)
        self.temporal_service = TemporalEvaluationService(self.service)
        self.verifier = verifier or ResultVerifier()

    @trace_stage("pipeline.evaluate", run_type="chain", tags=("pipeline", "evaluation"))
    def run(self, at_date: date) -> BatchEvaluationReport:
        run_id = str(uuid4())
        root_metadata = {
            "run_id": run_id,
            "evaluation_date": at_date.isoformat(),
            "pipeline": "covenant_evaluation",
        }
        annotate_current_trace(metadata=root_metadata)
        with trace_context(**root_metadata):
            covenants = self.registry.list()
            grouped: dict[tuple[str, str], list[object]] = {}
            for covenant in covenants:
                group_id = covenant.covenant_group_id or covenant.covenant_id
                for borrower_id in covenant.borrower_ids:
                    grouped.setdefault((borrower_id, group_id), []).append(covenant)

            results: list[CovenantResult] = []
            expected_pairs: list[tuple[str, str]] = []
            for (borrower_id, group_id), versions in sorted(grouped.items()):
                pair_metadata = {
                    "borrower_id": borrower_id,
                    "covenant_id": group_id,
                    "version_count": len(versions),
                }
                with trace_context(**pair_metadata):
                    result = self.temporal_service.evaluate_versions(  # type: ignore[arg-type]
                        versions,
                        borrower_id,
                        at_date,
                    )
                expected_pairs.append((result.borrower_id, result.covenant_id))
                results.append(result)
                self._save_result(result)

            verification = self.verifier.verify(expected_pairs, results)
            annotate_current_trace(
                metadata={
                    "expected_pair_count": len(expected_pairs),
                    "actual_pair_count": len(results),
                    "success_count": sum(result.status == "success" for result in results),
                    "partial_count": sum(result.status == "partial" for result in results),
                    "failed_count": sum(result.status == "failed" for result in results),
                    "verification_valid": verification.valid,
                }
            )
            return BatchEvaluationReport(
                run_id=run_id,
                evaluation_date=at_date,
                expected_pair_count=len(expected_pairs),
                actual_pair_count=len(results),
                results=results,
                verification=verification,
            )

    @trace_stage("pipeline.evaluate.persist", run_type="tool", tags=("storage", "evaluation"))
    def _save_result(self, result: CovenantResult) -> None:
        self.store.connection.execute(
            """
            INSERT INTO covenant_results VALUES (?, ?, CAST(? AS JSON))
            ON CONFLICT (borrower_id, covenant_id) DO UPDATE SET
                result_json = excluded.result_json
            """,
            [result.borrower_id, result.covenant_id, result.model_dump_json()],
        )
