from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from halyk_covenants.domain import CovenantResult, CovenantSpec, EvidenceMode
from halyk_covenants.evaluators.comparator import compare
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

    def evaluate(
        self,
        covenant: CovenantSpec,
        borrower_id: str,
        db: DuckDBStore,
        evaluation_date: date | None = None,
    ) -> CovenantResult:
        where_sql, parameters = build_where_clause(
            borrower_id,
            covenant.transaction_filters,
            time_window=covenant.time_window,
            evaluation_date=evaluation_date,
        )
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
            except Exception as exc:  # Evidence failure must not discard correct components.
                result.status = "partial"
                result.errors.append(f"evidence selection failed: {exc}")
        return result

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
        raise NotImplementedError(
            f"{covenant.evidence_mode.value} is not supported for {self.metric_type}"
        )
