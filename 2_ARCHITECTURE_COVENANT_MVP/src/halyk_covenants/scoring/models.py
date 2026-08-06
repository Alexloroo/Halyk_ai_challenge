from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

STATUS_WEIGHT = 0.50
ACTUAL_WEIGHT = 0.30
EVIDENCE_WEIGHT = 0.20
#: Relative error at which the actual score reaches zero.
ACTUAL_TOLERANCE = 0.05


class CellScore(BaseModel):
    """Score for one (scenario, clause) cell, decomposed by component."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    clause: str
    status_points: float = Field(ge=0.0, le=STATUS_WEIGHT)
    actual_points: float = Field(ge=0.0, le=ACTUAL_WEIGHT)
    evidence_points: float = Field(ge=0.0, le=EVIDENCE_WEIGHT)
    reason: str = ""

    @property
    def total(self) -> float:
        return self.status_points + self.actual_points + self.evidence_points


class ScoreReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cells: list[CellScore]
    missing_cells: list[str] = Field(default_factory=list)
    extra_cells: list[str] = Field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(cell.total for cell in self.cells)

    @property
    def maximum(self) -> float:
        return len(self.cells) + len(self.missing_cells)

    @property
    def status_accuracy(self) -> float:
        if not self.cells:
            return 0.0
        correct = sum(1 for c in self.cells if c.status_points == STATUS_WEIGHT)
        return correct / self.maximum

    @property
    def actual_within_tolerance(self) -> float:
        if not self.cells:
            return 0.0
        within = sum(1 for c in self.cells if c.actual_points > 0)
        return within / self.maximum

    @property
    def evidence_exact(self) -> float:
        """Share of cells whose key carries a real txn id that we matched."""
        keyed = [c for c in self.cells if "evidence:" in c.reason]
        if not keyed:
            return 0.0
        hit = sum(1 for c in keyed if c.evidence_points == EVIDENCE_WEIGHT)
        return hit / len(keyed)
