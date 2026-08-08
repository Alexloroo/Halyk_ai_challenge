"""End to end: dataset in, submission.json out.

Three phases, in order, each depending only on the one before:

    load        ledger, documents, template
    interpret   pick the current agreement, read clauses, categorise, resolve
                related parties
    compute     evaluate each cell, look for deciding evidence, fill the template

Every cell in the template gets an answer. A cell that cannot be computed still
gets a best guess, because the case scores a blank exactly as it scores a wrong
one.
"""

from __future__ import annotations

import json
import re
from contextlib import nullcontext
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import paths
from .audit import (
    AuditAdjustment,
    apply_adjustments,
    extract_adjustments,
    extract_fx_rates,
    extract_group_capex,
    is_actionable_audit_text,
)
from .categorize import assess_category
from .docs import NON_BINDING, DocKind, Document, DocumentLoadIssue, Edition, load_documents, pick
from .evaluate import Answer, EvaluationTrace, evaluate, find_evidence
from .generic_formula import (
    CovenantMode,
    ExternalMetric,
    GenericFormulaSpec,
    MetricSource,
    requires_generic_replacement,
)
from .ledger import LedgerEntry, by_scenario, load_ledger
from .llm_capabilities import (
    CAPABILITY_SYSTEM_PROMPT,
    DOCUMENTARY_SYSTEM_PROMPT,
    GENERIC_VERIFIER_SYSTEM_PROMPT,
    METRIC_SYSTEM_PROMPT,
    CapabilityRequest,
    DocumentaryFactRequest,
    DocumentMetricRequest,
    EvidenceCandidate,
    GenericVerificationRequest,
    resolve_capabilities,
    resolve_document_metrics,
    resolve_documentary_facts,
    verify_generic_formulas,
)
from .llm_categorize import CategoryRequest, FlowDirection, resolve_categories
from .llm_documents import (
    DocumentClassificationRequest,
    EntityLinkCandidate,
    EntityLinkRequest,
    resolve_document_classifications,
    resolve_entity_links,
)
from .llm_extract import FormulaSpec, extract_formulas
from .llm_full_context import (
    CALCULATOR_SYSTEM_PROMPT,
    VERIFIER_SYSTEM_PROMPT,
    FullContextRequest,
    build_full_context_payload,
    resolve_full_context,
)
from .llm_rules import (
    SYSTEM_PROMPT as RULE_EXTRACTION_PROMPT,
)
from .llm_rules import (
    RuleExtractionRequest,
    resolve_missing_rules,
)
from .parties import extract_related_parties, mark_related, mark_unrestricted
from .quality import PrivateReadinessReport, assess_private_readiness
from .rules import Rule, RuleKind, extract_rules
from .tracing import TraceWriter
from .tracing.documents import trace_classified, trace_pymupdf
from .tracing.evaluation import trace_evaluation
from .tracing.formulas import trace_formulas
from .tracing.ledger import trace_account_mapping, trace_categorized, trace_loaded
from .tracing.scenario import (
    trace_audit_input,
    trace_audit_output,
    trace_parties,
    trace_rules,
    trace_selected,
)
from .tracing.template import trace_template


def _trace_stage(trace: TraceWriter | None, name: str):
    return trace.stage(name) if trace is not None else nullcontext()


def _needs_capability_fallback(
    existing_formula: FormulaSpec | None,
    proposal: GenericFormulaSpec,
) -> bool:
    if proposal.mode is CovenantMode.DOCUMENTARY:
        return True
    if proposal.mode is not CovenantMode.GENERIC_NUMERIC:
        return False
    if existing_formula is None:
        return True
    return any(
        requirement.source is MetricSource.DOCUMENT for requirement in proposal.required_metrics
    ) or requires_generic_replacement(proposal.expression)


def _full_context_reason(
    key: str,
    *,
    rule: Rule,
    formulas: dict[str, FormulaSpec],
    generic_formulas: dict[str, GenericFormulaSpec],
    capability_results: dict[str, object],
    generic_verifications: dict[str, object],
) -> str | None:
    if rule.kind not in (RuleKind.RATIO, RuleKind.UNKNOWN):
        return None
    capability = capability_results.get(key)
    capability_resolution = getattr(capability, "resolution", None)
    if capability_resolution is not None and capability_resolution.mode is CovenantMode.UNSUPPORTED:
        return "capability_unsupported"
    verification = generic_verifications.get(key)
    verification_resolution = getattr(verification, "resolution", None)
    if verification is not None and (
        verification_resolution is None or not verification_resolution.accepted
    ):
        return "generic_verification_failed"
    if key not in formulas and key not in generic_formulas:
        return "formula_unavailable"
    return None


@dataclass
class RunReport:
    answers: dict[str, dict[str, Answer]] = field(default_factory=dict)
    scenarios: int = 0
    rules_found: int = 0
    cells_expected: int = 0
    agreements_missing: list[str] = field(default_factory=list)
    parties_unresolved: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    private_readiness: PrivateReadinessReport | None = None


def account_map(entries: list[LedgerEntry]) -> dict[str, str]:
    """scenario_id -> account_id, taken from the ledger itself."""
    mapping: dict[str, str] = {}
    for entry in entries:
        if entry.scenario_id and entry.account_id:
            mapping.setdefault(entry.scenario_id, entry.account_id)
    return mapping


def load_template(path: Path) -> dict[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    answers = payload.get("answers") or {}
    return {scenario: list(clauses) for scenario, clauses in answers.items()}


def _fallback(scenario_id: str, clause: str, note: str) -> Answer:
    """Never leave a cell empty; a blank and a wrong answer cost the same."""
    from decimal import Decimal

    return Answer(
        scenario_id=scenario_id,
        clause=clause,
        status="COMPLIANT",
        actual=Decimal(0),
        note=note,
    )


_GROUP_CAPEX_CLAUSE = re.compile(
    r"капитальн\w+\s+затрат\w*\s+Группы|"
    r"(?:the\s+)?Group(?:'s)?\s+capital\s+expenditure|"
    r"capital\s+expenditure\s+of\s+the\s+Group",
    re.I,
)
_BORROWER_NAME = re.compile(r"([A-Z][A-Za-z\- ]+?(?:JSC|LLP))")
_CONSOLIDATED_STATEMENT = re.compile(
    r"consolidated\s+financial\s+statements|"
    r"консолидированн\w*\s+финансов\w*\s+отчетност\w*|"
    r"шоғырландырылған\s+қаржылық\s+есептілік",
    re.I,
)


def _group_capex_for(rule_text: str, documents: list[Document]) -> Decimal | None:
    """Read group capex from the matching consolidated PP&E movement note."""
    name_match = _BORROWER_NAME.search(rule_text)
    if not name_match:
        return None
    borrower = name_match.group(1)
    for document in documents:
        flat_text = " ".join(document.text.split())
        if borrower not in flat_text or "Consolidated Financial Statements" not in flat_text:
            continue
        capex = extract_group_capex(document.text)
        if capex is not None:
            return capex
    return None


def _resolve_group_capex_values(
    rules: dict[str, dict[str, Rule]],
    agreements: dict[str, Document | None],
    accounts: dict[str, str],
    documents: list[Document],
    *,
    use_llm: bool,
) -> tuple[dict[str, Decimal], dict[str, dict[str, object]]]:
    """Resolve statement ownership, while keeping all amount extraction deterministic."""
    values: dict[str, Decimal] = {}
    records: dict[str, dict[str, object]] = {}
    requests: list[EntityLinkRequest] = []
    candidate_documents: dict[str, dict[str, Document]] = {}

    for scenario_id, clauses in rules.items():
        for clause_id, rule in clauses.items():
            if _GROUP_CAPEX_CLAUSE.search(rule.text) is None:
                continue
            key = f"{scenario_id}/{clause_id}"
            record: dict[str, object] = {
                "source": None,
                "value": None,
                "llm_requested": False,
                "llm_result": None,
                "candidates": [],
            }
            records[key] = record
            deterministic = _group_capex_for(rule.text, documents)
            if deterministic is not None:
                values[key] = deterministic
                record["source"] = "deterministic_name_match"
                record["value"] = deterministic
                continue
            if not use_llm:
                continue

            account = accounts.get(scenario_id, "")
            agreement = agreements.get(scenario_id)
            if not account or agreement is None:
                continue
            eligible = sorted(
                (
                    document
                    for document in documents
                    if account in document.account_ids
                    and NON_BINDING.search(document.text) is None
                    and _CONSOLIDATED_STATEMENT.search(document.text) is not None
                    and extract_group_capex(document.text) is not None
                ),
                key=lambda document: str(document.path),
            )
            if not eligible:
                continue
            candidates = tuple(
                EntityLinkCandidate(
                    candidate_id=f"candidate-{index:03d}", text=document.text[:30000]
                )
                for index, document in enumerate(eligible, start=1)
            )
            candidate_documents[key] = dict(
                zip((candidate.candidate_id for candidate in candidates), eligible, strict=True)
            )
            record["llm_requested"] = True
            record["candidates"] = [
                {"candidate_id": candidate.candidate_id, "document": document.path}
                for candidate, document in zip(candidates, eligible, strict=True)
            ]
            requests.append(
                EntityLinkRequest(
                    key=key,
                    agreement_text=agreement.text[:30000],
                    candidates=candidates,
                )
            )

    results = resolve_entity_links(requests) if requests else {}
    for key, result in results.items():
        record = records[key]
        record["llm_result"] = result
        if result.resolution is None:
            continue
        document = candidate_documents[key].get(result.resolution.matched_candidate_id)
        if document is None:
            continue
        capex = extract_group_capex(document.text)
        if capex is None:
            continue
        values[key] = capex
        record["source"] = "llm_entity_link"
        record["document"] = document.path
        record["value"] = capex
    return values, records


def _evidence_candidates_for(
    scenario_id: str,
    accounts: dict[str, str],
    documents: list[Document],
    *,
    include_agreements: bool = True,
) -> tuple[EvidenceCandidate, ...]:
    account = accounts.get(scenario_id, "")
    if not account:
        return ()
    eligible = sorted(
        (
            document
            for document in documents
            if account in document.account_ids
            and document.edition is Edition.CURRENT
            and NON_BINDING.search(document.text) is None
            and (include_agreements or document.kind is not DocKind.CREDIT_AGREEMENT)
        ),
        key=lambda document: str(document.path),
    )
    return tuple(
        EvidenceCandidate(
            candidate_id=f"candidate-{index:03d}",
            source=str(document.path),
            text=document.text[:20000],
        )
        for index, document in enumerate(eligible, start=1)
    )


def solve(
    *,
    data_dir: Path | None = None,
    documents: list[Document] | None = None,
    use_llm: bool = True,
    trace: TraceWriter | None = None,
) -> RunReport:
    root = data_dir or paths.data_dir()
    with _trace_stage(trace, "01_template"):
        template = load_template(root / "submission_template.json")
        report = RunReport(
            scenarios=len(template),
            cells_expected=sum(len(v) for v in template.values()),
        )
        if trace is not None:
            trace_template(trace, template)

    with _trace_stage(trace, "02_ledger_loaded"):
        entries = load_ledger(root / "master_ledger_2025.csv", set(template))
        if trace is not None:
            trace_loaded(trace, entries)

    with _trace_stage(trace, "03_ledger_categorized"):
        categorization_records: list[dict[str, object]] = []
        category_requests: list[CategoryRequest] = []
        records_by_key: dict[str, dict[str, object]] = {}
        entries_by_key: dict[str, LedgerEntry] = {}
        for index, entry in enumerate(entries):
            assessment = assess_category(entry.description, is_inflow=entry.is_inflow)
            entry.category = assessment.category
            key = f"{entry.txn_id}#{index}"
            record: dict[str, object] = {
                "key": key,
                "txn_id": entry.txn_id,
                "description": entry.description,
                "counterparty": entry.counterparty,
                "direction": (
                    FlowDirection.INFLOW
                    if entry.is_inflow
                    else FlowDirection.OUTFLOW
                    if entry.is_outflow
                    else None
                ),
                "initial_category": assessment.category,
                "candidate_categories": assessment.candidates,
                "reason": assessment.reason,
                "needs_llm": assessment.needs_llm,
                "llm_requested": False,
                "llm_result": None,
                "final_category": assessment.category,
            }
            categorization_records.append(record)
            records_by_key[key] = record
            entries_by_key[key] = entry
            if assessment.needs_llm and use_llm and (entry.is_inflow or entry.is_outflow):
                direction = FlowDirection.INFLOW if entry.is_inflow else FlowDirection.OUTFLOW
                category_requests.append(
                    CategoryRequest(
                        key=key,
                        description=entry.description,
                        counterparty=entry.counterparty,
                        direction=direction,
                    )
                )
                record["llm_requested"] = True

        category_results = resolve_categories(category_requests) if category_requests else {}
        for key, result in category_results.items():
            record = records_by_key[key]
            record["llm_result"] = result
            if result.resolution is not None:
                entries_by_key[key].category = result.resolution.category
                record["final_category"] = result.resolution.category
        report.notes.append(
            f"Category LLM resolutions: "
            f"{sum(result.resolution is not None for result in category_results.values())}/"
            f"{len(category_results)}"
        )
        if trace is not None:
            trace_categorized(trace, entries, categorization_records)

    with _trace_stage(trace, "04_pymupdf"):
        document_issues: list[DocumentLoadIssue] = []
        docs = (
            documents
            if documents is not None
            else load_documents(root / "documents", issues=document_issues)
        )
        if trace is not None:
            trace_pymupdf(trace, docs, document_issues)

    with _trace_stage(trace, "05_documents_classified"):
        target_accounts = {entry.account_id for entry in entries if entry.account_id}
        document_records: list[dict[str, object]] = []
        document_requests: list[DocumentClassificationRequest] = []
        documents_by_key: dict[str, Document] = {}
        records_by_document_key: dict[str, dict[str, object]] = {}
        for document in docs:
            key = str(document.path)
            needs_llm = (
                document.kind is DocKind.UNKNOWN
                and bool(target_accounts.intersection(document.account_ids))
                and not is_actionable_audit_text(document.text)
            )
            record: dict[str, object] = {
                "key": key,
                "name": document.name,
                "initial_kind": document.kind,
                "initial_edition": document.edition,
                "needs_llm": needs_llm,
                "llm_requested": needs_llm and use_llm,
                "llm_result": None,
                "final_kind": document.kind,
                "final_edition": document.edition,
            }
            document_records.append(record)
            records_by_document_key[key] = record
            documents_by_key[key] = document
            if needs_llm and use_llm:
                document_requests.append(DocumentClassificationRequest(key=key, text=document.text))

        document_results = (
            resolve_document_classifications(document_requests) if document_requests else {}
        )
        for key, result in document_results.items():
            record = records_by_document_key[key]
            record["llm_result"] = result
            if result.resolution is None:
                continue
            document = documents_by_key[key]
            document.kind = result.resolution.kind
            if result.resolution.edition is not Edition.UNKNOWN:
                document.edition = result.resolution.edition
            record["final_kind"] = document.kind
            record["final_edition"] = document.edition
        report.notes.append(
            f"Document LLM resolutions: "
            f"{sum(result.resolution is not None for result in document_results.values())}/"
            f"{len(document_results)}"
        )
        if trace is not None:
            trace_classified(trace, docs, document_records)

    with _trace_stage(trace, "06_account_mapping"):
        accounts = account_map(entries)
        grouped = by_scenario(entries)
        if trace is not None:
            trace_account_mapping(trace, accounts)

    all_rules: dict[str, dict[str, Rule]] = {}
    rule_records: dict[str, dict[str, object]] = {}
    rule_requests: list[RuleExtractionRequest] = []
    selected_agreements: dict[str, Document | None] = {}
    selected_kyc: dict[str, Document | None] = {}
    audit_adjustments_by_scenario: dict[str, tuple[AuditAdjustment, ...]] = {}
    party_results: dict[str, object] = {}
    total_adjustments = 0
    parties_resolved = 0
    for scenario_id in template:
        account = accounts.get(scenario_id, "")
        scenario_entries = grouped.get(scenario_id, [])

        with _trace_stage(trace, "07_documents_selected"):
            audit_docs = [
                document
                for document in docs
                if (
                    document.kind is DocKind.AUDIT_NOTES
                    or (
                        document.kind is DocKind.UNKNOWN and is_actionable_audit_text(document.text)
                    )
                )
                and document.edition is Edition.CURRENT
                and account in document.account_ids
            ]
            kyc = pick(docs, DocKind.KYC, account)
            agreement = pick(docs, DocKind.CREDIT_AGREEMENT, account)
            selected_agreements[scenario_id] = agreement
            selected_kyc[scenario_id] = kyc
            if trace is not None:
                trace_selected(
                    trace,
                    scenario_id,
                    account,
                    agreement=agreement,
                    kyc=kyc,
                    audit_documents=audit_docs,
                )

        with _trace_stage(trace, "08_audit_and_fx"):
            if trace is not None:
                trace_audit_input(trace, scenario_id, scenario_entries)
            all_adjs = []
            fx_rates: dict[str, object] = {}
            for audit_document in audit_docs:
                all_adjs.extend(extract_adjustments(audit_document.text))
                fx_rates.update(extract_fx_rates(audit_document.text))
            total_adjustments += len(all_adjs)
            audit_adjustments_by_scenario[scenario_id] = tuple(all_adjs)
            if all_adjs:
                scenario_entries = apply_adjustments(scenario_entries, all_adjs)
                grouped[scenario_id] = scenario_entries
            if fx_rates:
                from decimal import Decimal

                for entry in scenario_entries:
                    if entry.currency in fx_rates and entry.amount is not None:
                        rate = fx_rates[entry.currency]
                        entry.amount = (Decimal(str(entry.amount)) * rate).quantize(Decimal("0.01"))
                        entry.currency = "USD"
                        entry.fx_converted = True
            if trace is not None:
                trace_audit_output(trace, scenario_id, all_adjs, fx_rates, scenario_entries)

        with _trace_stage(trace, "09_related_parties"):
            parties = None
            if kyc is not None:
                parties = extract_related_parties(scenario_id, kyc.text)
                if parties.resolved:
                    parties_resolved += 1
                    mark_related(scenario_entries, parties)
                else:
                    report.parties_unresolved.append(scenario_id)
                mark_unrestricted(scenario_entries, parties)
            else:
                report.parties_unresolved.append(scenario_id)
            if trace is not None:
                trace_parties(trace, scenario_id, parties, scenario_entries)
            party_results[scenario_id] = parties

        with _trace_stage(trace, "10_rules"):
            extracted = extract_rules(scenario_id, agreement.text) if agreement else {}
            rules: dict[str, Rule] = {
                clause: extracted[clause] for clause in template[scenario_id] if clause in extracted
            }
            if agreement is None:
                report.agreements_missing.append(scenario_id)
            all_rules[scenario_id] = rules
            for clause in template[scenario_id]:
                key = f"{scenario_id}/{clause}"
                found = clause in rules
                rule_records[key] = {
                    "source": "deterministic" if found else None,
                    "llm_requested": False,
                    "llm_result": None,
                    "rule": rules.get(clause),
                }
                if not found and agreement is not None and use_llm:
                    rule_records[key]["llm_requested"] = True
                    rule_requests.append(
                        RuleExtractionRequest(
                            key=key,
                            scenario_id=scenario_id,
                            clause=clause,
                            agreement_text=agreement.text[:60000],
                        )
                    )

    with _trace_stage(trace, "10_rules"):
        rule_results = resolve_missing_rules(rule_requests) if rule_requests else {}
        for key, result in rule_results.items():
            record = rule_records[key]
            record["llm_result"] = result
            if result.rule is None:
                continue
            scenario_id, clause = key.split("/", maxsplit=1)
            all_rules[scenario_id][clause] = result.rule
            record["source"] = "llm_fallback"
            record["rule"] = result.rule
        report.rules_found = sum(len(rules) for rules in all_rules.values())
        report.notes.append(
            f"Rule LLM resolutions: "
            f"{sum(result.rule is not None for result in rule_results.values())}/"
            f"{len(rule_results)}"
        )
        if trace is not None:
            trace.write_text("10_rules", "system_prompt.txt", RULE_EXTRACTION_PROMPT)
            trace.write_json("10_rules", "decisions.json", rule_records)
            for scenario_id, rules in all_rules.items():
                trace_rules(trace, scenario_id, rules)

    if trace is not None:
        trace.update_stage("07_documents_selected", scenarios=len(template))
        trace.update_stage(
            "08_audit_and_fx", scenarios=len(template), adjustments=total_adjustments
        )
        trace.update_stage("09_related_parties", scenarios=len(template), resolved=parties_resolved)
        trace.update_stage(
            "10_rules",
            scenarios=len(template),
            rules=report.rules_found,
            rule_llm_requested=len(rule_results),
            rule_llm_resolved=sum(result.rule is not None for result in rule_results.values()),
        )

    with _trace_stage(trace, "11_formulas"):
        formulas: dict[str, FormulaSpec] = {}
        generic_formulas: dict[str, GenericFormulaSpec] = {}
        generic_candidates: dict[str, GenericFormulaSpec] = {}
        external_metrics: dict[str, dict[str, ExternalMetric]] = {}
        documentary_facts: dict[str, bool] = {}
        capability_records: dict[str, dict[str, object]] = {}
        if use_llm:
            formulas = extract_formulas(all_rules)
            report.notes.append(f"LLM formulas extracted: {len(formulas)}")

            capability_requests = [
                CapabilityRequest(
                    key=f"{scenario_id}/{clause}",
                    rule=rule,
                    existing_formula=formulas.get(f"{scenario_id}/{clause}"),
                )
                for scenario_id, rules in all_rules.items()
                for clause, rule in rules.items()
                if rule.kind in (RuleKind.RATIO, RuleKind.UNKNOWN)
            ]
            capability_results = resolve_capabilities(capability_requests)
            for request in capability_requests:
                result = capability_results[request.key]
                spec = result.resolution
                capability_records[request.key] = {
                    "rule": request.rule,
                    "existing_formula": request.existing_formula,
                    "verification": result,
                    "effective_path": (
                        CovenantMode.EXISTING_FORMULA
                        if request.existing_formula is not None
                        else "capability_failed"
                    ),
                }
                if spec is None:
                    continue
                if _needs_capability_fallback(request.existing_formula, spec):
                    generic_candidates[request.key] = spec
                    capability_records[request.key]["effective_path"] = (
                        "pending_generic_verification"
                    )
                elif spec.mode is CovenantMode.UNSUPPORTED and request.existing_formula is None:
                    capability_records[request.key]["effective_path"] = CovenantMode.UNSUPPORTED

            capability_requests_by_key = {request.key: request for request in capability_requests}
            verification_requests = [
                GenericVerificationRequest(
                    key=key,
                    rule=capability_requests_by_key[key].rule,
                    plan=spec,
                )
                for key, spec in generic_candidates.items()
            ]
            generic_verifications = verify_generic_formulas(verification_requests)
            for request in verification_requests:
                verification = generic_verifications[request.key]
                capability_records[request.key]["generic_verification"] = verification
                accepted = verification.resolution is not None and verification.resolution.accepted
                if accepted:
                    generic_formulas[request.key] = request.plan
                    formulas.pop(request.key, None)
                    capability_records[request.key]["effective_path"] = request.plan.mode
                else:
                    capability_records[request.key]["effective_path"] = (
                        "existing_formula_after_rejected_generic"
                        if request.key in formulas
                        else "rejected_generic"
                    )

            metric_requests: list[DocumentMetricRequest] = []
            fact_requests: list[DocumentaryFactRequest] = []
            for key, spec in generic_formulas.items():
                scenario_id = key.split("/", maxsplit=1)[0]
                candidates = _evidence_candidates_for(scenario_id, accounts, docs)
                if not candidates:
                    continue
                if spec.mode is CovenantMode.GENERIC_NUMERIC:
                    for requirement in spec.required_metrics:
                        if requirement.source is not MetricSource.DOCUMENT:
                            continue
                        metric_requests.append(
                            DocumentMetricRequest(
                                key=f"{key}::{requirement.name}",
                                metric=requirement.name,
                                description=requirement.description,
                                evidence_terms=tuple(requirement.evidence_terms),
                                candidates=candidates,
                            )
                        )
                elif spec.mode is CovenantMode.DOCUMENTARY and spec.documentary_requirement:
                    documentary_candidates = _evidence_candidates_for(
                        scenario_id,
                        accounts,
                        docs,
                        include_agreements=False,
                    )
                    if not documentary_candidates:
                        continue
                    fact_requests.append(
                        DocumentaryFactRequest(
                            key=key,
                            requirement=spec.documentary_requirement,
                            candidates=documentary_candidates,
                        )
                    )

            metric_results = resolve_document_metrics(metric_requests)
            metric_trace_records = {
                request.key: {
                    "metric": request.metric,
                    "description": request.description,
                    "evidence_terms": request.evidence_terms,
                    "candidates": [
                        {
                            "candidate_id": candidate.candidate_id,
                            "source": candidate.source,
                        }
                        for candidate in request.candidates
                    ],
                    "result": metric_results[request.key],
                }
                for request in metric_requests
            }
            for request in metric_requests:
                result = metric_results[request.key]
                formula_key = request.key.rsplit("::", maxsplit=1)[0]
                if result.metric is not None:
                    external_metrics.setdefault(formula_key, {})[request.metric] = result.metric
            fact_results = resolve_documentary_facts(fact_requests)
            fact_trace_records = {
                request.key: {
                    "requirement": request.requirement,
                    "candidates": [
                        {
                            "candidate_id": candidate.candidate_id,
                            "source": candidate.source,
                        }
                        for candidate in request.candidates
                    ],
                    "result": fact_results[request.key],
                }
                for request in fact_requests
            }
            for request in fact_requests:
                result = fact_results[request.key]
                if result.resolution is not None:
                    documentary_facts[request.key] = result.resolution.fact_present
            report.notes.append(
                f"Generic formulas: {len(generic_formulas)}; "
                f"document metrics: {sum(len(values) for values in external_metrics.values())}; "
                f"documentary facts: {len(documentary_facts)}"
            )
        else:
            capability_results = {}
            generic_verifications = {}
            metric_results = {}
            metric_trace_records = {}
            fact_results = {}
            fact_trace_records = {}
        group_capex_values, entity_link_records = _resolve_group_capex_values(
            all_rules,
            selected_agreements,
            accounts,
            docs,
            use_llm=use_llm,
        )
        report.notes.append(
            f"Group capex documents resolved: {len(group_capex_values)}/{len(entity_link_records)}"
        )
        full_context_records: dict[str, dict[str, object]] = {}
        full_context_requests: list[FullContextRequest] = []
        if use_llm:
            for scenario_id, clauses in all_rules.items():
                agreement = selected_agreements.get(scenario_id)
                if agreement is None:
                    continue
                for clause, rule in clauses.items():
                    key = f"{scenario_id}/{clause}"
                    reason = _full_context_reason(
                        key,
                        rule=rule,
                        formulas=formulas,
                        generic_formulas=generic_formulas,
                        capability_results=capability_results,
                        generic_verifications=generic_verifications,
                    )
                    if reason is None or rule.threshold is None:
                        continue
                    request = FullContextRequest(
                        key=key,
                        rule=rule,
                        account_id=accounts.get(scenario_id, ""),
                        agreement_text=agreement.text,
                        ledger=tuple(grouped.get(scenario_id, [])),
                        audit_adjustments=audit_adjustments_by_scenario.get(scenario_id, ()),
                        candidates=_evidence_candidates_for(scenario_id, accounts, docs),
                        external_metrics=external_metrics.get(key, {}),
                        kyc_text=(
                            selected_kyc[scenario_id].text
                            if selected_kyc.get(scenario_id) is not None
                            else ""
                        ),
                    )
                    full_context_requests.append(request)
                    full_context_records[key] = {
                        "reason": reason,
                        "request": build_full_context_payload(request),
                    }
            full_context_results = resolve_full_context(full_context_requests)
            for key, result in full_context_results.items():
                full_context_records[key]["result"] = result
        else:
            full_context_results = {}
        if trace is not None:
            trace_formulas(trace, all_rules, formulas, enabled=use_llm)
            trace.write_text("11_formulas", "capability_prompt.txt", CAPABILITY_SYSTEM_PROMPT)
            trace.write_text(
                "11_formulas", "generic_verifier_prompt.txt", GENERIC_VERIFIER_SYSTEM_PROMPT
            )
            trace.write_text("11_formulas", "metric_prompt.txt", METRIC_SYSTEM_PROMPT)
            trace.write_text("11_formulas", "documentary_prompt.txt", DOCUMENTARY_SYSTEM_PROMPT)
            trace.write_text(
                "11_formulas", "full_context_calculator_prompt.txt", CALCULATOR_SYSTEM_PROMPT
            )
            trace.write_text(
                "11_formulas", "full_context_verifier_prompt.txt", VERIFIER_SYSTEM_PROMPT
            )
            trace.write_json("11_formulas", "capabilities.json", capability_records)
            trace.write_json("11_formulas", "generic_formulas.json", generic_formulas)
            trace.write_json("11_formulas", "generic_verifications.json", generic_verifications)
            trace.write_json("11_formulas", "document_metrics.json", metric_trace_records)
            trace.write_json("11_formulas", "documentary_facts.json", fact_trace_records)
            trace.write_json("11_formulas", "entity_links.json", entity_link_records)
            trace.write_json("11_formulas", "full_context.json", full_context_records)
            trace.update_stage(
                "11_formulas",
                capability_verified=sum(
                    result.resolution is not None for result in capability_results.values()
                ),
                capability_failed=sum(
                    result.resolution is None for result in capability_results.values()
                ),
                generic_formulas=len(generic_formulas),
                generic_verified=sum(
                    result.resolution is not None and result.resolution.accepted
                    for result in generic_verifications.values()
                ),
                unsupported_formulas=sum(
                    result.resolution is not None
                    and result.resolution.mode is CovenantMode.UNSUPPORTED
                    for result in capability_results.values()
                ),
                document_metrics=sum(len(values) for values in external_metrics.values()),
                documentary_facts=len(documentary_facts),
                entity_links=sum(
                    record["source"] == "llm_entity_link" for record in entity_link_records.values()
                ),
                group_capex_resolved=len(group_capex_values),
                full_context_requested=len(full_context_requests),
                full_context_accepted=sum(
                    result.accepted for result in full_context_results.values()
                ),
            )

    with _trace_stage(trace, "12_evaluation"):
        evaluation_details: dict[str, EvaluationTrace] = {}
        for scenario_id, clauses in template.items():
            scenario_entries = grouped.get(scenario_id, [])
            rules = all_rules.get(scenario_id, {})
            cells: dict[str, Answer] = {}
            for clause in clauses:
                rule = rules.get(clause)
                if rule is None:
                    cells[clause] = _fallback(scenario_id, clause, "no rule extracted")
                    if trace is not None:
                        trace_evaluation(
                            trace,
                            scenario_id,
                            clause,
                            rule=None,
                            formula=None,
                            entries=scenario_entries,
                            details=None,
                            answer=cells[clause],
                            evidence_trials={},
                        )
                    continue
                formula = formulas.get(f"{scenario_id}/{clause}")
                key = f"{scenario_id}/{clause}"
                generic_formula = generic_formulas.get(key)
                formula_external_metrics = external_metrics.get(key, {})
                documentary_fact = documentary_facts.get(key)
                full_context_result = full_context_results.get(key)
                full_context_calculation = (
                    full_context_result.calculation
                    if full_context_result is not None and full_context_result.accepted
                    else None
                )
                numerator_constant = (
                    group_capex_values.get(key) if _GROUP_CAPEX_CLAUSE.search(rule.text) else None
                )
                details = EvaluationTrace()
                if full_context_result is not None and not full_context_result.accepted:
                    answer = _fallback(scenario_id, clause, "full_context_rejected")
                    details.scope_txn_ids = [entry.txn_id for entry in scenario_entries]
                    details.branch = "full_context_rejected"
                    details.comparator = rule.comparator
                    details.threshold = rule.threshold
                    details.actual = answer.actual
                    details.status = answer.status
                    details.note = answer.note
                else:
                    answer = evaluate(
                        rule,
                        scenario_entries,
                        formula=formula,
                        generic_formula=generic_formula,
                        external_metrics=formula_external_metrics,
                        documentary_fact=documentary_fact,
                        trace=details,
                        numerator_constant=numerator_constant,
                        full_context_calculation=full_context_calculation,
                    )
                evaluation_details[f"{scenario_id}/{clause}"] = details
                evidence_trials: dict[str, str] = {}
                if full_context_result is None:
                    answer.evidence_txn_id = find_evidence(
                        rule,
                        scenario_entries,
                        answer,
                        formula=formula,
                        generic_formula=generic_formula,
                        external_metrics=formula_external_metrics,
                        documentary_fact=documentary_fact,
                        trials=evidence_trials if trace is not None else None,
                        numerator_constant=numerator_constant,
                    )
                cells[clause] = answer
                if trace is not None:
                    trace_evaluation(
                        trace,
                        scenario_id,
                        clause,
                        rule=rule,
                        formula=formula,
                        generic_formula=generic_formula,
                        external_metrics=formula_external_metrics,
                        documentary_fact=(fact_results.get(key) if key in fact_results else None),
                        full_context_result=full_context_result,
                        entries=scenario_entries,
                        details=details,
                        answer=answer,
                        evidence_trials=evidence_trials,
                    )
            report.answers[scenario_id] = cells
        if trace is not None:
            trace.update_stage("12_evaluation", cells=report.cells_expected)

        report.private_readiness = assess_private_readiness(
            template=template,
            grouped=grouped,
            agreements=selected_agreements,
            parties=party_results,
            rules=all_rules,
            formulas=formulas,
            evaluations=evaluation_details,
            categorization_records=categorization_records,
            document_issues=document_issues,
            llm_enabled=use_llm,
            generic_formulas=generic_formulas,
            capability_results=capability_results,
            generic_verifications=generic_verifications,
            external_metrics=external_metrics,
            documentary_facts=documentary_facts,
            full_context_results=full_context_results,
        )
        if trace is not None:
            trace.write_root_json("private_readiness.json", report.private_readiness)
            trace.write_json("12_evaluation", "private_readiness.json", report.private_readiness)
            trace.update_stage(
                "12_evaluation",
                private_readiness=report.private_readiness.status,
                private_readiness_failures=report.private_readiness.checks["failures"],
                private_readiness_warnings=report.private_readiness.checks["warnings"],
            )

    return report


def to_submission(
    report: RunReport,
    template_path: Path,
    *,
    team: str = "ML Empire",
    contact_email: str = "voronkoleha00@gmail.com",
    model: str = "deterministic-v1",
) -> dict:
    """Fill the template in place: never add, rename or drop a key."""
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    payload["team"] = team
    payload["contact_email"] = contact_email
    payload["model"] = model

    for scenario_id, clauses in payload.get("answers", {}).items():
        for clause in clauses:
            answer = report.answers.get(scenario_id, {}).get(clause)
            if answer is None:
                clauses[clause] = {
                    "status": "COMPLIANT",
                    "actual": 0.0,
                    "evidence_txn_id": None,
                }
                continue
            clauses[clause] = {
                "status": answer.status,
                "actual": answer.rounded(),
                "evidence_txn_id": answer.evidence_txn_id,
            }
    return payload
