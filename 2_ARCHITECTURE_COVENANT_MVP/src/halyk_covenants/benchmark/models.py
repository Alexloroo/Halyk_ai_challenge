from decimal import Decimal

from pydantic import BaseModel, ConfigDict


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

