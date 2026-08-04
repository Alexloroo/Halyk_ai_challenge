from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from halyk_covenants.domain import (
    Calculation,
    CovenantResult,
    CovenantSpec,
    EvidenceMode,
    FailureStage,
)
from halyk_covenants.evaluators.comparator import compare
from halyk_covenants.observability import annotate_current_trace, trace_stage
from halyk_covenants.sql import build_where_clause
from halyk_covenants.storage import DuckDBStore

Numeric = Decimal | int


class CovenantEvaluator(Protocol):
    def evaluate(
        self,
        covenant: CovenantSpec,
        borrower_id: str,
        db: DuckDBStore,
        evaluation_date: date | None = None,
    ) -> CovenantResult: ...


class AggregateEvaluator:
    metric_type: str

    @trace_stage(
        "evaluation.metric",
        run_type="tool",
        tags=("evaluation", "deterministic"),
        failure_stage=FailureStage.CALCULATION,
    )
    def evaluate(
        self,
        covenant: CovenantSpec,
        borrower_id: str,
        db: DuckDBStore,
        evaluation_date: date | None = None,
    ) -> CovenantResult:
        borrower_scope = covenant.borrower_ids if covenant.scope_mode == "group" else borrower_id
        filters = [*covenant.transaction_filters, *covenant.metric.filters]
        exclusions = [*covenant.exclusions, *covenant.metric.exclusions]
        where_sql, parameters = build_where_clause(
            borrower_scope,
            filters,
            exclusions=exclusions,
            date_field=covenant.date_field,
            time_window=covenant.time_window,
            evaluation_date=evaluation_date,
            effective_from=covenant.effective_from,
            effective_to=covenant.effective_to,
        )
        annotate_current_trace(
            metadata={
                "metric_type": covenant.metric.metric_type,
                "filter_count": len(filters),
                "exclusion_count": len(exclusions),
                "parameter_count": len(parameters),
                "date_field": covenant.date_field,
                "time_window": covenant.time_window.type if covenant.time_window else None,
                "effective_from": (
                    covenant.effective_from.isoformat() if covenant.effective_from else None
                ),
                "effective_to": (
                    covenant.effective_to.isoformat() if covenant.effective_to else None
                ),
            }
        )
        self._validate_currency_scope(covenant, db, where_sql, parameters)
        value = self.calculate(covenant, db, where_sql, parameters)
        number_unit = covenant.metric.unit or covenant.condition.unit or covenant.condition.currency
        if value is None:
            return CovenantResult(
                borrower_id=borrower_id,
                covenant_id=covenant.covenant_id,
                verdict="unknown",
                number=None,
                number_unit=number_unit,
                status="partial",
                failure_stage=FailureStage.CALCULATION,
                errors=["metric is undefined for an empty transaction set"],
            )
        if covenant.condition.threshold is None:
            raise ValueError("condition threshold is required for deterministic evaluation")

        calculation_id = self._record_calculation(
            covenant=covenant,
            borrower_id=borrower_id,
            db=db,
            where_sql=where_sql,
            parameters=parameters,
            value=value,
            unit=number_unit,
        )
        verdict = (
            "complied"
            if compare(value, covenant.condition.comparator, covenant.condition.threshold)
            else "violated"
        )
        result = CovenantResult(
            borrower_id=borrower_id,
            covenant_id=covenant.covenant_id,
            verdict=verdict,
            number=value,
            number_unit=number_unit,
            calculation_id=calculation_id,
            status="success",
        )
        annotate_current_trace(
            metadata={
                "metric_value": str(value),
                "comparator": covenant.condition.comparator,
                "threshold": (
                    str(covenant.condition.threshold)
                    if covenant.condition.threshold is not None
                    else None
                ),
                "verdict": verdict,
                "calculation_id": calculation_id,
            }
        )
        if verdict == "violated" and covenant.evidence_mode != EvidenceMode.NONE:
            try:
                result.evidence_transaction_id = self.select_evidence(
                    covenant, borrower_id, db, where_sql, parameters
                )
                if result.evidence_transaction_id is None:
                    result.status = "partial"
                    result.failure_stage = FailureStage.EVIDENCE
                    result.errors.append("required evidence transaction not found")
                else:
                    from halyk_covenants.evidence import EvidenceContext, EvidenceValidator

                    verification = EvidenceValidator().validate(
                        result.evidence_transaction_id,
                        EvidenceContext(
                            covenant=covenant,
                            borrower_id=borrower_id,
                            db=db,
                            where_sql=where_sql,
                            parameters=parameters,
                        ),
                    )
                    if not verification.valid:
                        result.status = "partial"
                        result.failure_stage = FailureStage.EVIDENCE
                        result.errors.extend(verification.errors)
            except Exception as exc:
                result.status = "partial"
                result.failure_stage = FailureStage.EVIDENCE
                result.errors.append(f"evidence selection failed: {exc}")
            if result.failure_stage == FailureStage.EVIDENCE:
                annotate_current_trace(
                    metadata={
                        "failure_stage": FailureStage.EVIDENCE.value,
                        "evidence_transaction_id": result.evidence_transaction_id,
                    },
                    tags=(FailureStage.EVIDENCE.value,),
                )
        return result

    def _record_calculation(
        self,
        *,
        covenant: CovenantSpec,
        borrower_id: str,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
        value: Numeric,
        unit: str | None,
    ) -> str:
        calculation_sql = self.calculation_sql(covenant, where_sql)
        parameter_summary = [str(parameter) for parameter in parameters]
        identity = {
            "covenant_id": covenant.covenant_id,
            "borrower_id": borrower_id,
            "borrower_ids": sorted(covenant.borrower_ids),
            "metric": covenant.metric.model_dump(mode="json"),
            "sql": calculation_sql,
            "parameters": parameter_summary,
            "value": str(value),
            "unit": unit,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:24]
        calculation_id = f"calc-{digest}"
        row_count = int(
            db.connection.execute(
                f"SELECT COUNT(*) FROM transactions {where_sql}", parameters
            ).fetchone()[0]
        )
        calculation = Calculation(
            calculation_id=calculation_id,
            covenant_id=covenant.covenant_id,
            borrower_ids=identity["borrower_ids"],
            metric_type=covenant.metric.metric_type,
            sql=calculation_sql,
            parameter_summary=parameter_summary,
            input_row_count=row_count,
            value=value,
            unit=unit,
            created_at=datetime.now(UTC),
        )
        db.connection.execute(
            """
            INSERT INTO calculations VALUES (?, ?, ?, CAST(? AS JSON))
            ON CONFLICT (calculation_id) DO UPDATE SET
                covenant_id = excluded.covenant_id,
                borrower_id = excluded.borrower_id,
                calculation_json = excluded.calculation_json
            """,
            [calculation_id, covenant.covenant_id, borrower_id, calculation.model_dump_json()],
        )
        return calculation_id

    def calculation_sql(self, covenant: CovenantSpec, where_sql: str) -> str | None:
        metric_type = covenant.metric.metric_type
        field = covenant.metric.field
        if metric_type in {"sum", "max", "min", "avg"} and field:
            return f"SELECT {metric_type.upper()}({field}) FROM transactions {where_sql}"
        if metric_type == "count":
            return f"SELECT COUNT({field or '*'}) FROM transactions {where_sql}"
        if metric_type == "existence":
            return f"SELECT COUNT(*) > 0 FROM transactions {where_sql}"
        return (
            f"-- {metric_type} evaluator over filtered transaction scope\n"
            f"SELECT * FROM transactions {where_sql}"
        )

    def _validate_currency_scope(
        self,
        covenant: CovenantSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> None:
        if not _metric_uses_amount(covenant.metric):
            return
        currencies = [
            row[0]
            for row in db.connection.execute(
                f"""
                SELECT DISTINCT currency
                FROM transactions
                {where_sql} AND currency IS NOT NULL
                ORDER BY currency
                """,
                parameters,
            ).fetchall()
        ]
        if len(currencies) > 1:
            raise ValueError(
                f"mixed currencies require an explicit FX policy: {', '.join(currencies)}"
            )
        expected = covenant.condition.currency
        if expected and currencies and currencies[0].upper() != expected.upper():
            raise ValueError(
                f"metric currency {currencies[0]} does not match covenant currency {expected}"
            )

    def calculate(
        self,
        covenant: CovenantSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> Numeric | None:
        raise NotImplementedError

    def select_evidence(
        self,
        covenant: CovenantSpec,
        borrower_id: str,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> str | None:
        from halyk_covenants.evidence import EvidenceContext, EvidenceSelectorRegistry

        return EvidenceSelectorRegistry().select(
            EvidenceContext(
                covenant=covenant,
                borrower_id=borrower_id,
                db=db,
                where_sql=where_sql,
                parameters=parameters,
            )
        )


def _metric_uses_amount(metric: object) -> bool:
    field = getattr(metric, "field", None)
    if field == "amount":
        return True
    return any(
        child is not None and _metric_uses_amount(child)
        for child in (getattr(metric, "numerator", None), getattr(metric, "denominator", None))
    )
