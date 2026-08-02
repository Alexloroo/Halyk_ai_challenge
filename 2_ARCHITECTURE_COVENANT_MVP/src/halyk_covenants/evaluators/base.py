from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from halyk_covenants.domain import CovenantResult, CovenantSpec, EvidenceMode
from halyk_covenants.evaluators.comparator import compare
from halyk_covenants.observability import trace_stage
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

    @trace_stage("evaluation.metric", run_type="tool", tags=("evaluation", "deterministic"))
    def evaluate(
        self,
        covenant: CovenantSpec,
        borrower_id: str,
        db: DuckDBStore,
        evaluation_date: date | None = None,
    ) -> CovenantResult:
        borrower_scope = covenant.borrower_ids if covenant.scope_mode == "group" else borrower_id
        where_sql, parameters = build_where_clause(
            borrower_scope,
            covenant.transaction_filters,
            exclusions=covenant.exclusions,
            date_field=covenant.date_field,
            time_window=covenant.time_window,
            evaluation_date=evaluation_date,
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
                errors=["metric is undefined for an empty transaction set"],
            )
        if covenant.condition.threshold is None:
            raise ValueError("condition threshold is required for deterministic evaluation")

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
            status="success",
        )
        if verdict == "violated" and covenant.evidence_mode != EvidenceMode.NONE:
            try:
                result.evidence_transaction_id = self.select_evidence(
                    covenant, borrower_id, db, where_sql, parameters
                )
                if result.evidence_transaction_id is None:
                    result.status = "partial"
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
                        result.errors.extend(verification.errors)
            except Exception as exc:  # Evidence failure must not discard correct components.
                result.status = "partial"
                result.errors.append(f"evidence selection failed: {exc}")
        return result

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
