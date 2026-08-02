from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from halyk_covenants.observability import trace_stage

from .selectors import EvidenceContext


class EvidenceVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str] = Field(default_factory=list)


class EvidenceValidator:
    @trace_stage("evidence.validate", run_type="tool", tags=("verification", "evidence"))
    def validate(self, transaction_id: str, context: EvidenceContext) -> EvidenceVerification:
        row = context.db.connection.execute(
            f"""
            SELECT EXISTS(
                SELECT 1 FROM transactions
                {context.where_sql} AND transaction_id = ?
            )
            """,
            [*context.parameters, transaction_id],
        ).fetchone()
        if not bool(row[0]):
            return EvidenceVerification(
                valid=False,
                errors=["evidence transaction does not belong to the filtered covenant scope"],
            )
        return EvidenceVerification(valid=True)
