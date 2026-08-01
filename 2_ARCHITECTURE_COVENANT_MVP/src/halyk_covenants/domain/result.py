from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CovenantResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    borrower_id: str
    covenant_id: str
    verdict: Literal["complied", "violated", "unknown"]
    number: Decimal | int | None = None
    number_unit: str | None = None
    evidence_transaction_id: str | None = None
    calculation_id: str | None = None
    status: Literal["success", "partial", "failed"]
    errors: list[str] = Field(default_factory=list)
