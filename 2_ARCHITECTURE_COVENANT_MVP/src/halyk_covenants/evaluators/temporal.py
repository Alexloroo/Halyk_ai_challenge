from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from halyk_covenants.domain import CovenantResult, CovenantSpec, FailureStage, TimeWindowSpec
from halyk_covenants.evaluators.service import EvaluationService
from halyk_covenants.observability import annotate_current_trace, trace_context, trace_stage
from halyk_covenants.sql import window_bounds

_TRANSACTION_LEVEL_METRICS = frozenset({"max", "min"})


class TemporalEvaluationService:
    """Evaluate covenant amendments that become effective inside one metric window.

    For transaction-level extrema, every transaction segment can be evaluated against the rule
    version active on that transaction date. Aggregate metrics such as SUM/AVG/COUNT are rejected
    when a version changes inside one aggregate window because blindly splitting the aggregate
    changes its business meaning.
    """

    def __init__(self, service: EvaluationService) -> None:
        self.service = service

    @trace_stage(
        "evaluation.temporal_versions",
        run_type="chain",
        tags=("evaluation", "temporal"),
        failure_stage=FailureStage.TEMPORAL,
    )
    def evaluate_versions(
        self,
        versions: list[CovenantSpec],
        borrower_id: str,
        evaluation_date: date,
    ) -> CovenantResult:
        if not versions:
            return self._failed(borrower_id, "unknown", "no covenant versions supplied")

        group_id = versions[0].covenant_group_id or versions[0].covenant_id
        candidates = [
            item
            for item in versions
            if (item.covenant_group_id or item.covenant_id) == group_id
            and borrower_id in item.borrower_ids
        ]
        if not candidates:
            return self._failed(
                borrower_id,
                group_id,
                f"no covenant versions assigned to borrower {borrower_id}",
            )

        base_window = candidates[0].time_window
        bounds = window_bounds(base_window, evaluation_date)
        if bounds is None:
            # Timeless rules can still use the ordinary point-in-time resolver; there is no finite
            # aggregate interval to segment safely here.
            active = self._active_at(candidates, evaluation_date)
            if len(active) != 1:
                return self._failed(
                    borrower_id,
                    group_id,
                    "timeless covenant version resolution is ambiguous",
                )
            return self.service.evaluate(active[0], borrower_id, evaluation_date)

        start, end = bounds
        intersecting = [item for item in candidates if _intersects(item, start, end)]
        if not intersecting:
            return self._failed(
                borrower_id,
                group_id,
                "no covenant version intersects the metric window",
            )
        if len(intersecting) == 1:
            return self.service.evaluate(intersecting[0], borrower_id, evaluation_date)

        metric_types = {item.metric.metric_type for item in intersecting}
        if len(metric_types) != 1 or next(iter(metric_types)) not in _TRANSACTION_LEVEL_METRICS:
            return self._failed(
                borrower_id,
                group_id,
                "version changes inside one metric window are ambiguous for aggregate metrics",
            )

        ordered = sorted(
            intersecting,
            key=lambda item: (item.effective_from or date.min, item.covenant_id),
        )
        if _has_overlap(ordered):
            return self._failed(
                borrower_id,
                group_id,
                "overlapping covenant versions inside metric window",
            )

        segment_results: list[CovenantResult] = []
        for version in ordered:
            segment_start = max(start, version.effective_from or start)
            version_end = (
                version.effective_to + timedelta(days=1)
                if version.effective_to is not None
                else end
            )
            segment_end = min(end, version_end)
            if segment_start >= segment_end:
                continue
            clipped = version.model_copy(
                update={
                    "time_window": TimeWindowSpec(
                        type="custom",
                        start_date=segment_start,
                        end_date=segment_end - timedelta(days=1),
                    )
                }
            )
            with trace_context(
                covenant_version_id=version.covenant_id,
                segment_start=segment_start.isoformat(),
                segment_end_exclusive=segment_end.isoformat(),
            ):
                segment_results.append(
                    self.service.evaluate(clipped, borrower_id, evaluation_date)
                )

        if not segment_results:
            return self._failed(borrower_id, group_id, "no evaluable temporal segments")
        failed = [item for item in segment_results if item.status == "failed"]
        if failed:
            return self._failed(
                borrower_id,
                group_id,
                "; ".join(error for item in failed for error in item.errors)
                or "temporal segment evaluation failed",
            )

        violated = [item for item in segment_results if item.verdict == "violated"]
        chosen = _choose_supporting_result(violated or segment_results, next(iter(metric_types)))
        status = "success"
        failure_stage = None
        errors: list[str] = []
        if chosen.status == "partial":
            status = "partial"
            failure_stage = chosen.failure_stage
            errors = list(chosen.errors)
        verdict = "violated" if violated else (
            "unknown" if any(item.verdict == "unknown" for item in segment_results) else "complied"
        )
        annotate_current_trace(
            metadata={
                "temporal_segment_count": len(segment_results),
                "temporal_violation_count": len(violated),
                "verdict": verdict,
                "metric_value": str(chosen.number) if chosen.number is not None else None,
            }
        )
        return CovenantResult(
            borrower_id=borrower_id,
            covenant_id=group_id,
            verdict=verdict,
            number=chosen.number,
            number_unit=chosen.number_unit,
            evidence_transaction_id=chosen.evidence_transaction_id if verdict == "violated" else None,
            calculation_id=chosen.calculation_id,
            status=status,
            failure_stage=failure_stage,
            errors=errors,
        )

    @staticmethod
    def _active_at(candidates: list[CovenantSpec], at_date: date) -> list[CovenantSpec]:
        return [
            item
            for item in candidates
            if (item.effective_from is None or item.effective_from <= at_date)
            and (item.effective_to is None or at_date <= item.effective_to)
        ]

    @staticmethod
    def _failed(borrower_id: str, covenant_id: str, message: str) -> CovenantResult:
        annotate_current_trace(
            metadata={"failure_stage": FailureStage.TEMPORAL.value, "temporal_error": message},
            tags=(FailureStage.TEMPORAL.value,),
        )
        return CovenantResult(
            borrower_id=borrower_id,
            covenant_id=covenant_id,
            verdict="unknown",
            status="failed",
            failure_stage=FailureStage.TEMPORAL,
            errors=[message],
        )


def _intersects(covenant: CovenantSpec, start: date, end: date) -> bool:
    effective_start = covenant.effective_from or date.min
    effective_end = (
        covenant.effective_to + timedelta(days=1)
        if covenant.effective_to is not None
        else date.max
    )
    return effective_start < end and effective_end > start


def _has_overlap(ordered: list[CovenantSpec]) -> bool:
    previous_end: date | None = None
    for item in ordered:
        current_start = item.effective_from or date.min
        if previous_end is not None and current_start <= previous_end:
            return True
        previous_end = item.effective_to
        if previous_end is None:
            break
    return False


def _choose_supporting_result(
    results: list[CovenantResult],
    metric_type: str,
) -> CovenantResult:
    defined = [item for item in results if item.number is not None]
    if not defined:
        return results[0]
    if metric_type == "max":
        return max(defined, key=lambda item: Decimal(str(item.number)))
    if metric_type == "min":
        return min(defined, key=lambda item: Decimal(str(item.number)))
    return defined[0]
