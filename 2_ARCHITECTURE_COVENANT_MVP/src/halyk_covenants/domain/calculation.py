from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Calculation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calculation_id: str
    covenant_id: str
    borrower_ids: list[str] = Field(min_length=1)
    metric_type: str
    sql: str | None = None
    parameter_summary: list[str] = Field(default_factory=list)
    input_row_count: int = Field(ge=0)
    value: Decimal | int
    unit: str | None = None
    trace_id: str | None = None
    evaluator_version: str = "1"
    created_at: datetime | None = None


class PipelineStageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    trace_id: str | None = None
    parent_trace_id: str | None = None
    stage_name: str
    artifact_path: str | None = None
    status: Literal["success", "partial", "failed", "skipped"]
    started_at: datetime
    finished_at: datetime
    error_summary: str | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> "PipelineStageRecord":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot be before started_at")
        return self
