"""Pre-submission data-contract and calculation readiness checks."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .categorize import Category
from .docs import Document, DocumentLoadIssue
from .evaluate import EvaluationTrace
from .generic_formula import CovenantMode, ExternalMetric, GenericFormulaSpec, MetricSource
from .ledger import LedgerEntry
from .llm_extract import FormulaSpec
from .parties import RelatedParties
from .rules import Rule, RuleKind


@dataclass(frozen=True)
class QualityFinding:
    severity: str
    code: str
    message: str
    scenario_id: str | None = None
    clause: str | None = None
    subject: str | None = None


@dataclass
class PrivateReadinessReport:
    status: str
    checks: dict[str, int]
    findings: list[QualityFinding] = field(default_factory=list)


def assess_private_readiness(
    *,
    template: dict[str, list[str]],
    grouped: dict[str, list[LedgerEntry]],
    agreements: dict[str, Document | None],
    parties: dict[str, RelatedParties | None],
    rules: dict[str, dict[str, Rule]],
    formulas: dict[str, FormulaSpec],
    evaluations: dict[str, EvaluationTrace],
    categorization_records: list[dict[str, object]],
    document_issues: list[DocumentLoadIssue],
    llm_enabled: bool,
    generic_formulas: dict[str, GenericFormulaSpec] | None = None,
    capability_results: dict[str, object] | None = None,
    generic_verifications: dict[str, object] | None = None,
    external_metrics: dict[str, dict[str, ExternalMetric]] | None = None,
    documentary_facts: dict[str, bool] | None = None,
    full_context_results: dict[str, object] | None = None,
) -> PrivateReadinessReport:
    """Return actionable validation findings without changing solver output."""
    findings: list[QualityFinding] = []
    expected_cells = sum(len(clauses) for clauses in template.values())
    category_llm_requested = 0
    category_llm_resolved = 0
    generic_formulas = generic_formulas or {}
    capability_results = capability_results or {}
    generic_verifications = generic_verifications or {}
    external_metrics = external_metrics or {}
    documentary_facts = documentary_facts or {}
    full_context_results = full_context_results or {}

    def add(
        severity: str,
        code: str,
        message: str,
        scenario_id: str | None = None,
        clause: str | None = None,
        subject: str | None = None,
    ) -> None:
        findings.append(QualityFinding(severity, code, message, scenario_id, clause, subject))

    for issue in document_issues:
        add(
            "WARN",
            "document_load_issue",
            f"{issue.path.name}: {issue.operation}: {issue.error_type}: {issue.message}",
        )

    for record in categorization_records:
        if not record.get("needs_llm"):
            continue
        if not record.get("usable", True):
            continue
        if record.get("llm_requested"):
            category_llm_requested += 1
            result = record.get("llm_result")
            resolution = getattr(result, "resolution", None)
            if resolution is None:
                add(
                    "FAIL",
                    "category_llm_failed",
                    f"Semantic categorization failed for {record.get('txn_id')}",
                    subject=str(record.get("txn_id")),
                )
            else:
                category_llm_resolved += 1
                if resolution.category is Category.UNKNOWN:
                    add(
                        "FAIL",
                        "category_still_unknown",
                        f"No supported category for {record.get('txn_id')}",
                        subject=str(record.get("txn_id")),
                    )
        elif llm_enabled:
            add(
                "FAIL",
                "category_llm_not_requested",
                f"Semantic categorization could not run for {record.get('txn_id')}",
                subject=str(record.get("txn_id")),
            )

    extracted_rules = 0
    required_formulas = 0
    for scenario_id, clauses in template.items():
        entries = grouped.get(scenario_id, [])
        account_ids = sorted({entry.account_id for entry in entries if entry.account_id})
        if not entries:
            add("FAIL", "missing_ledger_rows", "No ledger rows selected", scenario_id)
        if not account_ids:
            add("FAIL", "missing_account", "No account ID found in ledger", scenario_id)
        elif len(account_ids) > 1:
            add(
                "FAIL",
                "multiple_accounts",
                f"Scenario maps to multiple accounts: {', '.join(account_ids)}",
                scenario_id,
            )

        if agreements.get(scenario_id) is None:
            add("FAIL", "missing_agreement", "Current credit agreement not found", scenario_id)

        scenario_rules = rules.get(scenario_id, {})
        requires_parties = any(
            rule.kind in (RuleKind.MAX_RELATED_PARTY, RuleKind.RELATED_PARTY_SHARE)
            and Category.ASSET_TRANSFER not in rule.categories
            for rule in scenario_rules.values()
        )
        party_result = parties.get(scenario_id)
        if requires_parties and (party_result is None or not party_result.resolved):
            add(
                "FAIL",
                "unresolved_related_parties",
                "KYC threshold is required by a related-party covenant",
                scenario_id,
            )

        unresolved_defects = [
            entry
            for entry in entries
            if any(defect != "audit_excluded" for defect in entry.defects)
        ]
        if unresolved_defects:
            defect_counts = Counter(
                defect
                for entry in unresolved_defects
                for defect in entry.defects
                if defect != "audit_excluded"
            )
            summary = ", ".join(f"{name}={count}" for name, count in sorted(defect_counts.items()))
            add(
                "FAIL",
                "unresolved_ledger_defects",
                f"Ledger defects remain after audit adjustments: {summary}",
                scenario_id,
            )

        unknown_count = sum(entry.category is Category.UNKNOWN for entry in entries)
        if unknown_count:
            add(
                "WARN",
                "unknown_ledger_categories",
                f"{unknown_count} ledger rows remain uncategorized",
                scenario_id,
            )

        for clause in clauses:
            key = f"{scenario_id}/{clause}"
            rule = scenario_rules.get(clause)
            if rule is None:
                add(
                    "FAIL",
                    "missing_rule",
                    "Covenant clause was not extracted",
                    scenario_id,
                    clause,
                )
                continue
            extracted_rules += 1
            generic = generic_formulas.get(key)
            if rule.threshold is None and (
                generic is None or generic.mode is not CovenantMode.DOCUMENTARY
            ):
                add(
                    "FAIL",
                    "missing_threshold",
                    "Covenant threshold was not extracted",
                    scenario_id,
                    clause,
                )
            if rule.period is None:
                add(
                    "WARN",
                    "missing_period",
                    "Covenant period was not extracted",
                    scenario_id,
                    clause,
                )
            if rule.kind in (RuleKind.RATIO, RuleKind.UNKNOWN):
                required_formulas += 1
                full_context = full_context_results.get(key)
                full_context_accepted = bool(getattr(full_context, "accepted", False))
                capability = capability_results.get(key)
                capability_resolution = getattr(capability, "resolution", None)
                if (
                    llm_enabled
                    and capability is not None
                    and capability_resolution is None
                    and key not in formulas
                    and key not in generic_formulas
                    and not full_context_accepted
                ):
                    add(
                        "FAIL",
                        "capability_verification_failed",
                        "Formula capability verification failed",
                        scenario_id,
                        clause,
                    )
                if (
                    capability_resolution is not None
                    and capability_resolution.mode is CovenantMode.UNSUPPORTED
                    and not full_context_accepted
                ):
                    add(
                        "WARN" if key in formulas else "FAIL",
                        ("formula_capability_gap" if key in formulas else "unsupported_formula"),
                        capability_resolution.reason,
                        scenario_id,
                        clause,
                    )
                if (
                    llm_enabled
                    and key not in formulas
                    and key not in generic_formulas
                    and not full_context_accepted
                ):
                    add(
                        "FAIL",
                        "missing_formula",
                        "Required LLM formula is unavailable",
                        scenario_id,
                        clause,
                    )
                verification = generic_verifications.get(key)
                verification_resolution = getattr(verification, "resolution", None)
                if (
                    verification is not None
                    and (verification_resolution is None or not verification_resolution.accepted)
                    and not full_context_accepted
                ):
                    issues = (
                        ", ".join(verification_resolution.issues)
                        if verification_resolution is not None
                        else "verifier unavailable"
                    )
                    add(
                        "WARN" if key in formulas else "FAIL",
                        (
                            "generic_formula_rejected_fallback_preserved"
                            if key in formulas
                            else "generic_formula_rejected"
                        ),
                        f"Generic formula verification failed: {issues}",
                        scenario_id,
                        clause,
                    )
                if generic is not None:
                    missing_metrics = [
                        requirement.name
                        for requirement in generic.required_metrics
                        if requirement.source is MetricSource.DOCUMENT
                        and requirement.name not in external_metrics.get(key, {})
                    ]
                    if missing_metrics and not full_context_accepted:
                        add(
                            "FAIL",
                            "missing_document_metric",
                            f"Document metrics unavailable: {', '.join(sorted(missing_metrics))}",
                            scenario_id,
                            clause,
                        )
                    if (
                        generic.mode is CovenantMode.DOCUMENTARY
                        and key not in documentary_facts
                        and not full_context_accepted
                    ):
                        add(
                            "FAIL",
                            "missing_documentary_fact",
                            "Documentary requirement could not be resolved",
                            scenario_id,
                            clause,
                        )
                    elif (
                        generic.mode is CovenantMode.DOCUMENTARY
                        and documentary_facts.get(key) is False
                    ):
                        add(
                            "WARN",
                            "documentary_absence_unverifiable",
                            "No supporting evidence was found; "
                            "absence is not independently provable",
                            scenario_id,
                            clause,
                        )

                if full_context is not None and not full_context_accepted:
                    add(
                        "FAIL",
                        "full_context_rejected",
                        getattr(full_context, "error", None)
                        or "Calculator and independent verifier did not agree",
                        scenario_id,
                        clause,
                    )

            details = evaluations.get(key)
            if details is not None:
                for flag in details.quality_flags:
                    severity = (
                        "FAIL"
                        if flag
                        in {
                            "missing_threshold",
                            "missing_formula",
                            "zero_denominator",
                            "unsupported_formula",
                            "missing_documentary_fact",
                            "full_context_rejected",
                        }
                        or flag.startswith("missing_metric:")
                        or flag.startswith("unsupported_operator:")
                        else "WARN"
                    )
                    add(severity, flag, flag.replace("_", " "), scenario_id, clause)

    findings = list(
        {
            (
                finding.severity,
                finding.code,
                finding.scenario_id,
                finding.clause,
                finding.subject,
            ): finding
            for finding in findings
        }.values()
    )
    severities = Counter(finding.severity for finding in findings)
    status = "FAIL" if severities["FAIL"] else "WARN" if severities["WARN"] else "PASS"
    return PrivateReadinessReport(
        status=status,
        checks={
            "scenarios": len(template),
            "expected_cells": expected_cells,
            "extracted_rules": extracted_rules,
            "required_formulas": required_formulas,
            "available_formulas": len(formulas),
            "generic_formulas": len(generic_formulas),
            "external_metrics": sum(len(values) for values in external_metrics.values()),
            "documentary_facts": len(documentary_facts),
            "full_context_accepted": sum(
                bool(getattr(result, "accepted", False)) for result in full_context_results.values()
            ),
            "category_llm_requested": category_llm_requested,
            "category_llm_resolved": category_llm_resolved,
            "document_load_issues": len(document_issues),
            "failures": severities["FAIL"],
            "warnings": severities["WARN"],
        },
        findings=findings,
    )
