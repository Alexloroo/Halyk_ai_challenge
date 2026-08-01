from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, StrictStr


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: StrictStr
    borrower_id: StrictStr | None = None
    account_id: StrictStr | None = None
    transaction_date: date
    amount: Decimal
    currency: str | None = None
    direction: str | None = None
    counterparty_id: StrictStr | None = None
    counterparty_name: str | None = None
    purpose: str | None = None
    source_row_id: StrictStr | None = None
