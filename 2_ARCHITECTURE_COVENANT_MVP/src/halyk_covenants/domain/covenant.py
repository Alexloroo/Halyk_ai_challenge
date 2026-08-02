from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from halyk_covenants.domain.source import SourceRef
from halyk_covenants.domain.transaction_fields import DATE_FIELDS, GROUP_BY_FIELDS, PHYSICAL_TRANSACTION_FIELDS

# Backwards-compatible public constant used by evaluator modules. Filters use the broader
# FILTER_FIELDS catalog in covenant validation/SQL because derived fields are executable too.
TRANSACTION_FIELDS = PHYSICAL_TRANSACTION_FIELDS

MetricType = Literal["sum", "count", "max", "min", "avg", "ratio", "existence", "frequency"]
Comparator = Literal["<", "<=", ">", ">=", "==", "!="]
FilterOperator = Literal[
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "contains",
    "not_contains",
]
WindowType = Literal[
    "calendar_day",
    "calendar_week",
    "calendar_month",
    "calendar_quarter",
    "calendar_year",
    "rolling_days",
    "custom",
    "none",
]


class EvidenceMode(StrEnum):
    NONE = "none"
    VIOLATING_TRANSACTION = "violating_transaction"
    TRIGGER_TRANSACTION = "trigger_transaction"
    MAX_TRANSACTION = "max_transaction"


class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_type: MetricType
    field: str | None = None
    numerator: MetricSpec | None = None
    denominator: MetricSpec | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def validate_metric_shape(self) -> MetricSpec:
        if self.metric_type == "ratio":
            if self.numerator is None or self.denominator is None:
                raise ValueError("ratio metrics require numerator and denominator")
            if self.field is not None:
                raise ValueError("ratio metrics cannot define a direct field")
        elif self.numerator is not None or self.denominator is not None:
            raise ValueError("nested numerator/denominator are valid only for ratio metrics")
        return self


class ConditionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparator: Comparator
    threshold: Decimal | int | None
    unit: str | None = None
    currency: str | None = None


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    operator: FilterOperator
    value: Any


class TimeWindowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WindowType
    rolling_days: int | None = None
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_window_parameters(self) -> TimeWindowSpec:
        if self.type == "rolling_days" and (self.rolling_days is None or self.rolling_days <= 0):
            raise ValueError("rolling_days windows require a positive rolling_days value")
        if self.type == "custom":
            if self.start_date is None or self.end_date is None:
                raise ValueError("custom windows require start_date and end_date")
            if self.start_date > self.end_date:
                raise ValueError("custom window start_date cannot be after end_date")
        return self


class CovenantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    covenant_id: str
    covenant_group_id: str | None = None
    raw_text: str
    borrower_ids: list[str] = Field(min_length=1)
    scope_mode: Literal["per_borrower", "group"] = "per_borrower"
    metric: MetricSpec
    condition: ConditionSpec
    transaction_filters: list[FilterSpec] = Field(default_factory=list)
    exclusions: list[FilterSpec] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    date_field: str = "transaction_date"
    time_window: TimeWindowSpec | None = None
    evidence_mode: EvidenceMode = EvidenceMode.NONE
    effective_from: date | None = None
    effective_to: date | None = None
    source: SourceRef
    confidence: float = Field(ge=0, le=1)
    status: Literal["compiled", "unsupported", "failed_compilation"] = "compiled"
    compiler_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_execution_scope(self) -> CovenantSpec:
        if self.scope_mode == "group" and len(self.borrower_ids) < 2:
            raise ValueError("group scope requires at least two borrowers")
        unknown_group_fields = set(self.group_by) - GROUP_BY_FIELDS
        if unknown_group_fields:
            unknown = ", ".join(sorted(unknown_group_fields))
            raise ValueError(f"unsupported group_by fields: {unknown}")
        if self.date_field not in DATE_FIELDS:
            raise ValueError(f"unsupported date_field: {self.date_field}")
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("effective_from cannot be after effective_to")
        if self.status == "compiled":
            if self.condition.threshold is None:
                raise ValueError("compiled covenant requires a deterministic condition threshold")
            if self.metric.metric_type in {"sum", "max", "min", "avg"} and self.metric.field is None:
                raise ValueError(f"compiled {self.metric.metric_type} metric requires a field")
        return self


MetricSpec.model_rebuild()
