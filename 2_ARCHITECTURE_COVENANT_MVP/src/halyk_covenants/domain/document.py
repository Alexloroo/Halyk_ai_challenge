from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.domain.source import SourceRef

ExtractionRoute = Literal["native", "ocr", "layout", "failed"]
BlockType = Literal["text", "table", "table_cell", "image", "header", "footer"]


class PageExtractionQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    native_text_chars: int = Field(ge=0)
    text_density: Decimal = Field(ge=0)
    image_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    route: ExtractionRoute
    confidence: Decimal = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)


class DocumentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    document_id: str
    page: int = Field(ge=1)
    block_type: BlockType
    text: str
    bbox: tuple[float, float, float, float] | None = None
    table_id: str | None = None
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)
    borrower_ids: list[str] = Field(default_factory=list)
    extraction_method: ExtractionRoute
    confidence: Decimal = Field(ge=0, le=1)
    source: SourceRef
