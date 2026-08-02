from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from halyk_covenants.domain import CovenantSpec, EvidenceMode
from halyk_covenants.observability import trace_stage
from halyk_covenants.storage import DuckDBStore


@dataclass(frozen=True)
class EvidenceContext:
    covenant: CovenantSpec
    borrower_id: str
    db: DuckDBStore
    where_sql: str
    parameters: list[object]


class EvidenceSelector(Protocol):
    def select(self, context: EvidenceContext) -> str | None: ...


class FirstViolatingSelector:
    def select(self, context: EvidenceContext) -> str | None:
        predicate = ""
        if context.covenant.metric.metric_type in {"max", "min"}:
            field = context.covenant.metric.field
            threshold = context.covenant.condition.threshold
            if field is None or threshold is None:
                return None
            inverse = {
                "<=": ">",
                "<": ">=",
                ">=": "<",
                ">": "<=",
                "==": "!=",
                "!=": "=",
            }[context.covenant.condition.comparator]
            predicate = f" AND {field} {inverse} ?"
            parameters = [*context.parameters, threshold]
        else:
            parameters = context.parameters
        row = context.db.connection.execute(
            f"""
            SELECT transaction_id
            FROM transactions
            {context.where_sql}{predicate}
            ORDER BY transaction_date ASC, transaction_id ASC
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        return None if row is None else str(row[0])


class MaxTransactionSelector:
    def select(self, context: EvidenceContext) -> str | None:
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
        return None if row is None else str(row[0])


class TriggerTransactionSelector:
    def select(self, context: EvidenceContext) -> str | None:
        threshold = context.covenant.condition.threshold
        if threshold is None:
            return None
        offset = _trigger_offset(threshold, context.covenant.condition.comparator)
        if context.covenant.metric.metric_type == "frequency":
            return self._frequency_trigger(context, offset)
        row = context.db.connection.execute(
            f"""
            SELECT transaction_id
            FROM transactions
            {context.where_sql}
            ORDER BY transaction_date ASC, transaction_id ASC
            LIMIT 1 OFFSET ?
            """,
            [*context.parameters, offset],
        ).fetchone()
        return None if row is None else str(row[0])

    def _frequency_trigger(self, context: EvidenceContext, offset: int) -> str | None:
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
            [*context.parameters, offset + 1],
        ).fetchone()
        return None if row is None else str(row[0])


def _trigger_offset(threshold: Decimal | int, comparator: str) -> int:
    numeric = int(threshold)
    if Decimal(str(threshold)) != Decimal(numeric):
        raise ValueError("transaction count threshold must be an integer")
    if comparator == "<=":
        return max(numeric, 0)
    if comparator == "<":
        return max(numeric - 1, 0)
    raise ValueError(f"trigger transaction is unsupported for comparator {comparator}")


class EvidenceSelectorRegistry:
    def __init__(self) -> None:
        self.selectors: dict[EvidenceMode, EvidenceSelector] = {
            EvidenceMode.VIOLATING_TRANSACTION: FirstViolatingSelector(),
            EvidenceMode.TRIGGER_TRANSACTION: TriggerTransactionSelector(),
            EvidenceMode.MAX_TRANSACTION: MaxTransactionSelector(),
        }

    @trace_stage("evidence.select", run_type="tool", tags=("evaluation", "evidence"))
    def select(self, context: EvidenceContext) -> str | None:
        if context.covenant.evidence_mode == EvidenceMode.NONE:
            return None
        selector = self.selectors.get(context.covenant.evidence_mode)
        if selector is None:
            raise ValueError(f"unsupported evidence mode: {context.covenant.evidence_mode}")
        return selector.select(context)
