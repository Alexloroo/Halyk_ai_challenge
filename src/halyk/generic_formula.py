"""Safe expression language and deterministic interpreter for novel covenants."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from .categorize import OPEX_LIKE, Category
from .ledger import LedgerEntry


class CovenantMode(StrEnum):
    EXISTING_FORMULA = "existing_formula"
    GENERIC_NUMERIC = "generic_numeric"
    DOCUMENTARY = "documentary"
    UNSUPPORTED = "unsupported"


class ExpressionOp(StrEnum):
    CONSTANT = "constant"
    METRIC = "metric"
    SUM_INFLOW = "sum_inflow"
    SUM_OUTFLOW = "sum_outflow"
    MAX_TRANSACTION = "max_transaction"
    MAX_CATEGORY = "max_category"
    COUNT = "count"
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    MIN = "min"
    MAX = "max"
    AVERAGE = "average"
    ABS = "abs"


class MetricSource(StrEnum):
    LEDGER = "ledger"
    DOCUMENT = "document"


class MetricRequirement(BaseModel):
    name: str = Field(description="Stable snake_case financial metric name")
    source: MetricSource
    description: str = Field(description="Meaning of the metric in this covenant")
    evidence_terms: list[str] = Field(
        default_factory=list,
        description="Exact or near-exact document labels expected for this metric",
    )


class ExpressionSpec(BaseModel):
    op: ExpressionOp
    args: list[ExpressionSpec] = Field(default_factory=list)
    value: Decimal | None = None
    metric: str | None = None
    categories: list[Category] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_metric_operator(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        metric_aliases = {
            "revenue",
            "financing_inflow",
            "ebitda",
            "related_party_outflow",
            "unrestricted_transfer",
            "total_outflow",
            "total_inflow",
        }
        operator = value.get("op")
        if operator in metric_aliases:
            normalized = dict(value)
            normalized["op"] = ExpressionOp.METRIC
            normalized.setdefault("metric", operator)
            return normalized
        return value


class GenericFormulaSpec(BaseModel):
    mode: CovenantMode
    supported: bool
    reason: str
    clause_evidence: str
    expression: ExpressionSpec | None = None
    comparator: str = "<="
    condition: ExpressionSpec | None = None
    condition_comparator: str = ">"
    condition_threshold: Decimal | None = None
    required_metrics: list[MetricRequirement] = Field(default_factory=list)
    documentary_requirement: str | None = None
    expected_presence: bool = True


@dataclass(frozen=True)
class ExternalMetric:
    name: str
    value: Decimal
    source_document: str
    evidence: str
    value_text: str


@dataclass
class ExpressionResult:
    value: Decimal
    entries: list[LedgerEntry] = field(default_factory=list)
    intermediates: dict[str, Decimal] = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)


LEDGER_METRICS = {
    "revenue",
    "financing_inflow",
    "ebitda",
    "related_party_outflow",
    "unrestricted_transfer",
    "total_outflow",
    "total_inflow",
    *(
        category.value
        for category in Category
        if category not in {Category.CONTRA, Category.UNKNOWN}
    ),
}

DOCUMENT_METRICS = {
    "cash_balance",
    "total_debt",
    "net_debt",
    "equity",
    "current_assets",
    "current_liabilities",
    "inventory",
    "group_capex",
}

METRIC_REGISTRY = {
    **{name: MetricSource.LEDGER for name in LEDGER_METRICS},
    **{name: MetricSource.DOCUMENT for name in DOCUMENT_METRICS},
}

_LEAF_OPS = {
    ExpressionOp.CONSTANT,
    ExpressionOp.METRIC,
    ExpressionOp.SUM_INFLOW,
    ExpressionOp.SUM_OUTFLOW,
    ExpressionOp.MAX_TRANSACTION,
    ExpressionOp.MAX_CATEGORY,
    ExpressionOp.COUNT,
}
_UNARY_OPS = {ExpressionOp.ABS}
_BINARY_OPS = {
    ExpressionOp.SUBTRACT,
    ExpressionOp.MULTIPLY,
    ExpressionOp.DIVIDE,
}
_VARIADIC_OPS = {
    ExpressionOp.ADD,
    ExpressionOp.MIN,
    ExpressionOp.MAX,
    ExpressionOp.AVERAGE,
}
_ALLOWED_EXPRESSION_CATEGORIES = set(Category) - {Category.CONTRA, Category.UNKNOWN}


def validate_expression(expression: ExpressionSpec) -> list[str]:
    errors: list[str] = []
    nodes = 0

    def visit(node: ExpressionSpec, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 12:
            errors.append("expression depth exceeds 12")
            return
        if node.op in _LEAF_OPS and node.args:
            errors.append(f"{node.op} must not have args")
        if node.op in _UNARY_OPS and len(node.args) != 1:
            errors.append(f"{node.op} requires exactly one arg")
        if node.op in _BINARY_OPS and len(node.args) != 2:
            errors.append(f"{node.op} requires exactly two args")
        if node.op in _VARIADIC_OPS and not node.args:
            errors.append(f"{node.op} requires at least one arg")
        if node.op is ExpressionOp.CONSTANT and node.value is None:
            errors.append("constant requires value")
        if node.op is ExpressionOp.METRIC and not node.metric:
            errors.append("metric node requires metric name")
        invalid_categories = sorted(
            category.value
            for category in node.categories
            if category not in _ALLOWED_EXPRESSION_CATEGORIES
        )
        if invalid_categories:
            errors.append(f"unsupported expression categories: {invalid_categories}")
        for child in node.args:
            visit(child, depth + 1)

    visit(expression, 1)
    if nodes > 100:
        errors.append("expression contains more than 100 nodes")
    return sorted(set(errors))


def required_metric_names(expression: ExpressionSpec | None) -> set[str]:
    if expression is None:
        return set()
    names = (
        {expression.metric} if expression.op is ExpressionOp.METRIC and expression.metric else set()
    )
    for child in expression.args:
        names.update(required_metric_names(child))
    return names


def requires_generic_replacement(expression: ExpressionSpec | None) -> bool:
    """Return whether an AST exceeds the shapes already representable by FormulaSpec."""
    if expression is None:
        return True
    if expression.op in _LEAF_OPS:
        return False
    if expression.op is ExpressionOp.DIVIDE and len(expression.args) == 2:
        return any(child.op not in _LEAF_OPS for child in expression.args)
    if expression.op is ExpressionOp.SUBTRACT and len(expression.args) == 2:
        left, right = expression.args
        return not (
            left.op is ExpressionOp.METRIC
            and left.metric == "revenue"
            and right.op is ExpressionOp.MAX_CATEGORY
        )
    if expression.op is ExpressionOp.ADD:
        metrics = {child.metric for child in expression.args if child.op is ExpressionOp.METRIC}
        return not (
            len(metrics) == len(expression.args) and metrics == {"revenue", "financing_inflow"}
        )
    return True


def _unique(entries: list[LedgerEntry]) -> list[LedgerEntry]:
    return list({entry.txn_id: entry for entry in entries}.values())


def _sum(entries: list[LedgerEntry]) -> Decimal:
    return sum((entry.magnitude for entry in entries), Decimal(0))


def _categories(values: list[Category]) -> frozenset[Category]:
    return frozenset(values)


def _outflows(entries: list[LedgerEntry], categories: frozenset[Category]) -> list[LedgerEntry]:
    return [
        entry
        for entry in entries
        if entry.is_outflow
        and entry.category is not Category.CONTRA
        and (not categories or entry.category in categories)
    ]


def _inflows(entries: list[LedgerEntry], categories: frozenset[Category]) -> list[LedgerEntry]:
    return [
        entry
        for entry in entries
        if entry.is_inflow and (not categories or entry.category in categories)
    ]


def _metric(
    name: str,
    entries: list[LedgerEntry],
    external_metrics: dict[str, ExternalMetric],
    categories: frozenset[Category],
) -> ExpressionResult:
    if name in external_metrics:
        return ExpressionResult(external_metrics[name].value)
    category_by_name = {category.value: category for category in Category}
    if name == "revenue":
        selected = _inflows(entries, frozenset({Category.REVENUE}))
        return ExpressionResult(_sum(selected), selected)
    if name == "financing_inflow":
        selected = _inflows(entries, frozenset({Category.FINANCING}))
        return ExpressionResult(_sum(selected), selected)
    if name == "related_party_outflow":
        selected = [entry for entry in entries if entry.is_outflow and entry.is_related_party]
        return ExpressionResult(_sum(selected), selected)
    if name == "unrestricted_transfer":
        selected = [
            entry for entry in entries if entry.is_outflow and entry.is_unrestricted_transfer
        ]
        return ExpressionResult(_sum(selected), selected)
    if name == "total_outflow":
        selected = _outflows(entries, categories)
        return ExpressionResult(_sum(selected), selected)
    if name == "total_inflow":
        selected = _inflows(entries, categories)
        return ExpressionResult(_sum(selected), selected)
    if name == "ebitda":
        revenue = _inflows(entries, frozenset({Category.REVENUE}))
        expenses = _outflows(entries, categories or OPEX_LIKE)
        return ExpressionResult(_sum(revenue) - _sum(expenses), revenue + expenses)
    if name in category_by_name:
        selected = _outflows(entries, frozenset({category_by_name[name]}))
        return ExpressionResult(_sum(selected), selected)
    return ExpressionResult(Decimal(0), quality_flags=[f"missing_metric:{name}"])


def evaluate_expression(
    expression: ExpressionSpec,
    entries: list[LedgerEntry],
    external_metrics: dict[str, ExternalMetric],
) -> ExpressionResult:
    categories = _categories(expression.categories)
    if expression.op is ExpressionOp.CONSTANT:
        return ExpressionResult(expression.value or Decimal(0))
    if expression.op is ExpressionOp.METRIC:
        return _metric(expression.metric or "", entries, external_metrics, categories)
    if expression.op is ExpressionOp.SUM_INFLOW:
        selected = _inflows(entries, categories)
        return ExpressionResult(_sum(selected), selected)
    if expression.op is ExpressionOp.SUM_OUTFLOW:
        selected = _outflows(entries, categories)
        return ExpressionResult(_sum(selected), selected)
    if expression.op is ExpressionOp.MAX_TRANSACTION:
        selected = _outflows(entries, categories)
        largest = max(selected, key=lambda entry: entry.magnitude, default=None)
        return ExpressionResult(
            largest.magnitude if largest else Decimal(0), [largest] if largest else []
        )
    if expression.op is ExpressionOp.MAX_CATEGORY:
        candidates = categories or OPEX_LIKE
        totals = [
            (_sum(selected := _outflows(entries, frozenset({category}))), selected)
            for category in candidates
        ]
        value, selected = max(totals, key=lambda item: item[0], default=(Decimal(0), []))
        return ExpressionResult(value, selected)
    if expression.op is ExpressionOp.COUNT:
        selected = _outflows(entries, categories) if categories else entries
        return ExpressionResult(Decimal(len(selected)), list(selected))

    children = [evaluate_expression(child, entries, external_metrics) for child in expression.args]
    values = [child.value for child in children]
    selected = _unique([entry for child in children for entry in child.entries])
    flags = [flag for child in children for flag in child.quality_flags]
    intermediates = {key: value for child in children for key, value in child.intermediates.items()}
    if expression.op is ExpressionOp.ADD:
        value = sum(values, Decimal(0))
    elif expression.op is ExpressionOp.SUBTRACT:
        value = values[0] - values[1]
    elif expression.op is ExpressionOp.MULTIPLY:
        value = values[0] * values[1]
    elif expression.op is ExpressionOp.DIVIDE:
        value = values[0] / values[1] if values[1] else Decimal(0)
        if not values[1]:
            flags.append("zero_denominator")
    elif expression.op is ExpressionOp.MIN:
        value = min(values)
    elif expression.op is ExpressionOp.MAX:
        value = max(values)
    elif expression.op is ExpressionOp.AVERAGE:
        value = sum(values, Decimal(0)) / Decimal(len(values))
    elif expression.op is ExpressionOp.ABS:
        value = abs(values[0])
    else:
        value = Decimal(0)
        flags.append(f"unsupported_operator:{expression.op}")
    intermediates[f"node_{expression.op}_{len(intermediates)}"] = value
    return ExpressionResult(value, selected, intermediates, flags)
