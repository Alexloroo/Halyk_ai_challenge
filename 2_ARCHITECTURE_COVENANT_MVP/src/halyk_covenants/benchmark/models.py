from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.domain import CovenantResult
from halyk_covenants.synthetic.models import ExpectedAnswer


class CaseScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    number_score: int
    verdict_score: int
    evidence_score: int
    component_score: int
    full_exact_match: bool
    status_match: bool


class BenchmarkSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_cases: int
    earned_components: int
    maximum_components: int
    number_accuracy: Decimal
    verdict_accuracy: Decimal
    evidence_accuracy: Decimal
    component_accuracy: Decimal
    full_exact_match_accuracy: Decimal
    failed_case_ids: list[str]


class DataQualityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    severity: Literal["critical", "high", "medium", "low"]
    confidence: Literal["high", "medium", "low"]
    evidence: str
    risk: str
    remediation: str


class DataQualityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_count: int
    column_count: int
    exact_duplicate_rows: int
    duplicate_transaction_id_values: int
    null_counts: dict[str, int]
    borrower_ids: list[str]
    currencies: list[str]
    minimum_date: date
    maximum_date: date
    rows_are_chronologically_sorted: bool
    findings: list[DataQualityFinding]


class CaseBenchmarkResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str
    covenant_id: str
    borrower_id: str
    evaluation_date: date
    expected: ExpectedAnswer
    actual: CovenantResult
    score: CaseScore


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    benchmark_scope: str
    methodology: str
    summary: BenchmarkSummary
    status_counts: dict[str, int]
    failure_stage_counts: dict[str, int] = Field(default_factory=dict)
    data_quality: DataQualityProfile
    known_limitations: list[str]
    cases: list[CaseBenchmarkResult]
