from __future__ import annotations

from decimal import Decimal

from halyk_covenants.domain import CovenantSpec, FilterSpec, MetricSpec
from halyk_covenants.domain.covenant import TRANSACTION_FIELDS
from halyk_covenants.evaluators.base import AggregateEvaluator
from halyk_covenants.sql.filters import compile_filter
from halyk_covenants.storage import DuckDBStore

_SUPPORTED_COMPONENTS = frozenset({"sum", "count", "max", "min", "avg"})


class RatioEvaluator(AggregateEvaluator):
    metric_type = "ratio"

    def calculate(
        self,
        covenant: CovenantSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> Decimal | None:
        numerator = covenant.metric.numerator
        denominator = covenant.metric.denominator
        if numerator is None or denominator is None:
            raise ValueError("ratio requires numerator and denominator")
        self._validate_component(numerator, "numerator")
        self._validate_component(denominator, "denominator")

        denominator_where, denominator_parameters = _extend_scope(
            where_sql,
            parameters,
            denominator.filters,
            denominator.exclusions,
        )
        denominator_value = self._aggregate(
            denominator,
            db,
            denominator_where,
            denominator_parameters,
        )
        if denominator_value is None or Decimal(denominator_value) == 0:
            return None

        numerator_where, numerator_parameters = _extend_scope(
            where_sql,
            parameters,
            numerator.filters,
            numerator.exclusions,
        )
        if not covenant.group_by:
            numerator_value = self._aggregate(
                numerator,
                db,
                numerator_where,
                numerator_parameters,
            )
        else:
            unknown = set(covenant.group_by) - TRANSACTION_FIELDS
            if unknown:
                raise ValueError(f"unsupported ratio group fields: {sorted(unknown)}")
            group_sql = ", ".join(covenant.group_by)
            expression = self._aggregate_expression(numerator)
            row = db.connection.execute(
                f"""
                SELECT {expression} AS grouped_value
                FROM transactions
                {numerator_where}
                GROUP BY {group_sql}
                ORDER BY grouped_value DESC, {group_sql}
                LIMIT 1
                """,
                numerator_parameters,
            ).fetchone()
            numerator_value = None if row is None else row[0]
        if numerator_value is None:
            return None
        return Decimal(numerator_value) / Decimal(denominator_value)

    @staticmethod
    def _validate_component(metric: MetricSpec, label: str) -> None:
        if metric.metric_type not in _SUPPORTED_COMPONENTS:
            raise ValueError(
                f"ratio {label} metric {metric.metric_type!r} is unsupported; "
                f"expected one of {sorted(_SUPPORTED_COMPONENTS)}"
            )
        if metric.metric_type != "count" and metric.field != "amount":
            raise ValueError(f"ratio {label} {metric.metric_type} currently requires field=amount")
        if metric.metric_type == "count" and metric.field is not None and metric.field not in TRANSACTION_FIELDS:
            raise ValueError(f"ratio {label} count field is unsupported: {metric.field}")

    @classmethod
    def _aggregate(
        cls,
        metric: MetricSpec,
        db: DuckDBStore,
        where_sql: str,
        parameters: list[object],
    ) -> Decimal | int | None:
        expression = cls._aggregate_expression(metric)
        return db.connection.execute(
            f"SELECT {expression} FROM transactions {where_sql}", parameters
        ).fetchone()[0]

    @staticmethod
    def _aggregate_expression(metric: MetricSpec) -> str:
        if metric.metric_type == "count":
            return f"COUNT({metric.field or '*'})"
        assert metric.field is not None
        return f"{metric.metric_type.upper()}({metric.field})"


def _extend_scope(
    where_sql: str,
    parameters: list[object],
    filters: list[FilterSpec],
    exclusions: list[FilterSpec],
) -> tuple[str, list[object]]:
    predicates: list[str] = []
    output_parameters = list(parameters)
    for filter_spec in filters:
        predicate, values = compile_filter(filter_spec)
        predicates.append(predicate)
        output_parameters.extend(values)
    for exclusion in exclusions:
        predicate, values = compile_filter(exclusion)
        predicates.append(f"COALESCE(NOT ({predicate}), TRUE)")
        output_parameters.extend(values)
    if not predicates:
        return where_sql, output_parameters
    return f"{where_sql} AND {' AND '.join(predicates)}", output_parameters
