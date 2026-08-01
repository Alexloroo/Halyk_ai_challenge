from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.domain import Borrower, CovenantSpec, Transaction


class DocumentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    title: str
    borrower_ids: list[str]
    covenant_ids: list[str]
    defects: list[str] = Field(min_length=1)


class ExpectedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: Decimal | int | None
    verdict: Literal["complied", "violated", "unknown"]
    evidence_transaction_id: str | None = None
    status: Literal["success", "partial", "failed"]
    explanation: str


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str
    covenant_id: str
    borrower_id: str
    evaluation_date: date
    document_file: str
    expected: ExpectedAnswer


class SyntheticDatasetDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    documents: list[DocumentDefinition]
    borrowers: list[Borrower]
    transactions: list[Transaction]
    covenants: list[CovenantSpec]
    cases: list[BenchmarkCase]
    known_anomalies: list[dict[str, str]]

