from __future__ import annotations

import logging
from decimal import Decimal

from halyk_covenants.domain import Calculation, CovenantResult
from halyk_covenants.observability import trace_stage
from halyk_covenants.storage import DuckDBStore
from halyk_covenants.verification.models import VerificationIssue

logger = logging.getLogger(__name__)


class DualPathVerifier:
    def __init__(self, store: DuckDBStore) -> None:
        self.store = store

    @trace_stage(
        "verification.dual_path",
        run_type="tool",
        tags=("verification", "deterministic"),
    )
    def verify(
        self,
        result: CovenantResult,
        calculation: Calculation | None,
    ) -> list[VerificationIssue]:
        if calculation is None or calculation.sql is None:
            return []

        issues: list[VerificationIssue] = []
        try:
            params = [self._parse_param(p) for p in calculation.parameter_summary]
            row = self.store.connection.execute(calculation.sql, params).fetchone()
            if row is None:
                issues.append(VerificationIssue(
                    code="dual_path_null",
                    message="dual-path re-execution returned no rows",
                    classification="non_repairable",
                    borrower_id=result.borrower_id,
                    covenant_id=result.covenant_id,
                ))
                return issues

            recomputed = Decimal(str(row[0])) if row[0] is not None else Decimal("0")
            original = Decimal(str(calculation.value))

            if recomputed != original:
                issues.append(VerificationIssue(
                    code="dual_path_mismatch",
                    message=(
                        f"dual-path value {recomputed} differs from "
                        f"original {original}"
                    ),
                    classification="non_repairable",
                    borrower_id=result.borrower_id,
                    covenant_id=result.covenant_id,
                ))
        except Exception as exc:
            logger.warning(
                "Dual-path verification failed for %s/%s: %s",
                result.borrower_id, result.covenant_id, exc,
            )
            issues.append(VerificationIssue(
                code="dual_path_error",
                message=f"dual-path re-execution error: {exc}",
                classification="non_repairable",
                borrower_id=result.borrower_id,
                covenant_id=result.covenant_id,
            ))
        return issues

    @staticmethod
    def _parse_param(param_str: str) -> object:
        if param_str == "True":
            return True
        if param_str == "False":
            return False
        if param_str == "None":
            return None
        try:
            return int(param_str)
        except ValueError:
            pass
        try:
            return float(param_str)
        except ValueError:
            pass
        return param_str
