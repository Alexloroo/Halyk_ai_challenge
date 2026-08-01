from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from halyk_covenants.domain.source import SourceRef

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
    def validate_ratio_parts(self) -> MetricSpec:
        if self.metric_type == "ratio" and (self.numerator is None or self.denominator is None):
            raise ValueError("ratio metrics require numerator and denominator")
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
    raw_text: str
    borrower_ids: list[str] = Field(min_length=1)
    metric: MetricSpec
    condition: ConditionSpec
    transaction_filters: list[FilterSpec] = Field(default_factory=list)
    time_window: TimeWindowSpec | None = None
    evidence_mode: EvidenceMode = EvidenceMode.NONE
    effective_from: date | None = None
    effective_to: date | None = None
    source: SourceRef
    confidence: float = Field(ge=0, le=1)


MetricSpec.model_rebuild()
