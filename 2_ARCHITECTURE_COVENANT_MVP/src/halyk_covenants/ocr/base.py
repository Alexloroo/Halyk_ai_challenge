from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.domain import DocumentBlock


class OCRLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    bbox: tuple[float, float, float, float]
    confidence: Decimal = Field(ge=0, le=1)


class OCRProvider(Protocol):
    def extract(self, image: bytes, *, document_id: str, page: int) -> list[DocumentBlock]: ...
