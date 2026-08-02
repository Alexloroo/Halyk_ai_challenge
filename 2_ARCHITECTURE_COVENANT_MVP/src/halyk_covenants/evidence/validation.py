from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.domain import EvidenceMode
from halyk_covenants.observability import trace_stage

from .selectors import EvidenceContext


class EvidenceVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str] = Field(default_factory=list)


class EvidenceValidator:
    @trace_stage("evidence.validate", run_type="tool", tags=("verification", "evidence"))
    def validate(self, transaction_id: str, context: EvidenceContext) -> EvidenceVerification:
        row = context.db.connection.execute(
            f"""
            SELECT EXISTS(
                SELECT 1 FROM transactions
                {context.where_sql} AND transaction_id = ?
            )
            """,
            [*context.parameters, transaction_id],
        ).fetchone()
        if not bool(row[0]):
            return EvidenceVerification(
                valid=False,
                errors=["evidence transaction does not belong to the filtered covenant scope"],
            )

        mode = context.covenant.evidence_mode
        if mode == EvidenceMode.MAX_TRANSACTION:
            return self._validate_maximum(transaction_id, context)
        if mode == EvidenceMode.VIOLATING_TRANSACTION:
            return self._validate_violating(transaction_id, context)
        if mode == EvidenceMode.TRIGGER_TRANSACTION:
            return self._validate_trigger(transaction_id, context)
        return EvidenceVerification(valid=True)

    @staticmethod
    def _validate_maximum(
        transaction_id: str, context: EvidenceContext
    ) -> EvidenceVerification:
        field = context.covenant.metric.field or "amount"
        row = context.db.connection.execute(
            f"""
            SELECT transaction_id
            FROM transactions
            {context.where_sql}
            ORDER BY {field} DESC, transaction_date ASC, transaction_id ASC
            LIMIT 1
            """,
            context.parameters,
        ).fetchone()
        expected = None if row is None else str(row[0])
        if expected != transaction_id:
            return EvidenceVerification(
                valid=False,
                errors=[f"evidence transaction is not the selected maximum; expected {expected}"],
            )
        return EvidenceVerification(valid=True)

    @staticmethod
    def _validate_violating(
        transaction_id: str, context: EvidenceContext
    ) -> EvidenceVerification:
        metric_type = context.covenant.metric.metric_type
        field = context.covenant.metric.field
        threshold = context.covenant.condition.threshold
        if metric_type not in {"max", "min"} or field is None or threshold is None:
            return EvidenceVerification(valid=True)
        inverse = {
            "<=": ">",
            "<": ">=",
            ">=": "<",
            ">": "<=",
            "==": "!=",
            "!=": "=",
        }[context.covenant.condition.comparator]
        row = context.db.connection.execute(
            f"""
            SELECT EXISTS(
                SELECT 1 FROM transactions
                {context.where_sql} AND transaction_id = ? AND {field} {inverse} ?
            )
            """,
            [*context.parameters, transaction_id, threshold],
        ).fetchone()
        if not bool(row[0]):
            return EvidenceVerification(
                valid=False,
                errors=[
                    "evidence transaction does not individually violate the covenant threshold"
                ],
            )
        return EvidenceVerification(valid=True)

    @classmethod
    def _validate_trigger(
        cls, transaction_id: str, context: EvidenceContext
    ) -> EvidenceVerification:
        threshold = context.covenant.condition.threshold
        comparator = context.covenant.condition.comparator
        if threshold is None or comparator not in {"<", "<="}:
            return EvidenceVerification(
                valid=False,
                errors=["trigger evidence has unsupported threshold"],
            )
        numeric_threshold = int(threshold)
        if Decimal(str(threshold)) != Decimal(numeric_threshold):
            return EvidenceVerification(
                valid=False,
                errors=["trigger threshold must be an integer"],
            )
        position = numeric_threshold + 1 if comparator == "<=" else numeric_threshold
        position = max(position, 1)
        if context.covenant.metric.metric_type == "frequency":
            expected = cls._frequency_trigger(context, position)
        else:
            row = context.db.connection.execute(
                f"""
                SELECT transaction_id
                FROM transactions
                {context.where_sql}
                ORDER BY transaction_date ASC, transaction_id ASC
                LIMIT 1 OFFSET ?
                """,
                [*context.parameters, position - 1],
            ).fetchone()
            expected = None if row is None else str(row[0])
        if expected != transaction_id:
            return EvidenceVerification(
                valid=False,
                errors=[f"evidence transaction is not the threshold trigger; expected {expected}"],
            )
        return EvidenceVerification(valid=True)

    @staticmethod
    def _frequency_trigger(context: EvidenceContext, position: int) -> str | None:
        date_field = context.covenant.date_field
        row = context.db.connection.execute(
            f"""
            WITH filtered AS (
                SELECT
                    transaction_id,
                    CAST({date_field} AS DATE) AS bucket,
                    ROW_NUMBER() OVER (
                        PARTITION BY CAST({date_field} AS DATE)
                        ORDER BY {date_field} ASC, transaction_id ASC
                    ) AS position
                FROM transactions
                {context.where_sql}
            )
            SELECT transaction_id
            FROM filtered
            WHERE position = ?
            ORDER BY bucket ASC, transaction_id ASC
            LIMIT 1
            """,
            [*context.parameters, position],
        ).fetchone()
        return None if row is None else str(row[0])
