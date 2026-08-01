from pydantic import BaseModel, ConfigDict, Field, StrictStr


class Borrower(BaseModel):
    model_config = ConfigDict(extra="forbid")

    borrower_id: StrictStr
    canonical_name: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
