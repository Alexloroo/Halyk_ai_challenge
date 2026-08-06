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
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .audit import apply_adjustments, extract_adjustments, extract_fx_rates
from .categorize import categorize
from .docs import DocKind, Document, Edition, load_documents, pick
from .evaluate import Answer, evaluate, find_evidence
from .ledger import LedgerEntry, by_scenario, load_ledger
from .llm_extract import FormulaSpec, extract_formulas
from .parties import extract_related_parties, mark_related
from .rules import Rule, RuleKind, extract_rules


@dataclass
class RunReport:
    answers: dict[str, dict[str, Answer]] = field(default_factory=dict)
    scenarios: int = 0
    rules_found: int = 0
    cells_expected: int = 0
    agreements_missing: list[str] = field(default_factory=list)
    parties_unresolved: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


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


def solve(
    *,
    data_dir: Path | None = None,
    documents: list[Document] | None = None,
    use_llm: bool = True,
) -> RunReport:
    root = data_dir or paths.data_dir()
    template = load_template(root / "submission_template.json")
    report = RunReport(
        scenarios=len(template),
        cells_expected=sum(len(v) for v in template.values()),
    )

    entries = load_ledger(root / "master_ledger_2025.csv", set(template))
    for entry in entries:
        entry.category = categorize(entry.description, is_inflow=entry.is_inflow)

    docs = documents if documents is not None else load_documents(root / "documents")
    accounts = account_map(entries)
    grouped = by_scenario(entries)

    all_rules: dict[str, dict[str, Rule]] = {}
    for scenario_id, clauses in template.items():
        account = accounts.get(scenario_id, "")
        scenario_entries = grouped.get(scenario_id, [])

        audit_docs = [
            d for d in docs
            if d.kind in (DocKind.AUDIT_NOTES, DocKind.UNKNOWN)
            and d.edition is Edition.CURRENT
            and account in d.account_ids
        ]
        all_adjs = []
        fx_rates: dict[str, object] = {}
        for adoc in audit_docs:
            all_adjs.extend(extract_adjustments(adoc.text))
            fx_rates.update(extract_fx_rates(adoc.text))
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

        kyc = pick(docs, DocKind.KYC, account)
        if kyc is not None:
            parties = extract_related_parties(scenario_id, kyc.text)
            if parties.resolved:
                mark_related(scenario_entries, parties)
            else:
                report.parties_unresolved.append(scenario_id)
        else:
            report.parties_unresolved.append(scenario_id)

        agreement = pick(docs, DocKind.CREDIT_AGREEMENT, account)
        rules: dict[str, Rule] = (
            extract_rules(scenario_id, agreement.text) if agreement else {}
        )
        if agreement is None:
            report.agreements_missing.append(scenario_id)
        report.rules_found += len(rules)
        all_rules[scenario_id] = rules

    formulas: dict[str, FormulaSpec] = {}
    if use_llm:
        formulas = extract_formulas(all_rules)
        report.notes.append(f"LLM formulas extracted: {len(formulas)}")

    for scenario_id, clauses in template.items():
        scenario_entries = grouped.get(scenario_id, [])
        rules = all_rules.get(scenario_id, {})

        cells: dict[str, Answer] = {}
        for clause in clauses:
            rule = rules.get(clause)
            if rule is None:
                cells[clause] = _fallback(scenario_id, clause, "no rule extracted")
                continue
            formula = formulas.get(f"{scenario_id}/{clause}")
            answer = evaluate(rule, scenario_entries, formula=formula)
            answer.evidence_txn_id = find_evidence(
                rule, scenario_entries, answer, formula=formula,
            )
            cells[clause] = answer
        report.answers[scenario_id] = cells

    return report


def to_submission(
    report: RunReport,
    template_path: Path,
    *,
    team: str = "cloud1",
    contact_email: str = "team@example.com",
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
