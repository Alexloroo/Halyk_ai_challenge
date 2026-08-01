from pydantic import BaseModel, ConfigDict


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str | None = None
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    table_id: str | None = None
    row_id: str | None = None
    transaction_id: str | None = None
