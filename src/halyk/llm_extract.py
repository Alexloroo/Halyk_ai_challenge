"""LLM-powered clause interpretation via DeepSeek + LangChain.

Regex extracts the clause text and threshold reliably, but cannot parse
the *formula* — which categories go into the numerator vs denominator,
whether revenue is involved, whether it's a minimum or maximum.

One short call per clause, structured output via Pydantic, retry on errors.
"""

from __future__ import annotations

import os
import re
import time
from enum import StrEnum
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .rules import Rule

load_dotenv()


class OutputKind(StrEnum):
    RATIO = "ratio"
    DOLLAR_AMOUNT = "dollar_amount"


class AggKind(StrEnum):
    SUM_OUTFLOW = "sum_outflow"
    SUM_INFLOW = "sum_inflow"
    FINANCING_INFLOW = "financing_inflow"
    REVENUE_PLUS_FINANCING = "revenue_plus_financing"
    REVENUE = "revenue"
    EBITDA = "ebitda"
    MAX_SINGLE_CATEGORY = "max_single_category"
    REVENUE_MINUS_MAX_CATEGORY = "revenue_minus_max_category"
    RELATED_PARTY_OUTFLOW = "related_party_outflow"
    UNRESTRICTED_TRANSFER = "unrestricted_transfer"


class FormulaSpec(BaseModel):
    output_kind: OutputKind = Field(
        description="'ratio' if the covenant tests a ratio (e.g. capex/ebitda ≤ 0.42x). "
        "'dollar_amount' if the covenant tests an absolute dollar figure "
        "(e.g. personnel spend ≤ $4,000,000)."
    )
    numerator_agg: AggKind = Field(
        description="How to compute the numerator. "
        "sum_outflow: total outgoing in given categories. "
        "sum_inflow: total incoming in given categories. "
        "financing_inflow: inflows from financing activities only (loan drawdowns, "
        "facility proceeds — excludes revenue, refunds, interest income). "
        "revenue_plus_financing: sum of revenue PLUS financing inflows "
        "(when the covenant tests 'revenue and financing proceeds' combined). "
        "revenue: inflows classified as revenue. "
        "ebitda: revenue minus all operating expenses. "
        "max_single_category: the LARGEST category total among the listed categories "
        "(each category summed separately, then take the max). "
        "revenue_minus_max_category: revenue MINUS the largest of the listed categories "
        "(e.g. revenue minus max(personnel, tax)). "
        "related_party_outflow: total outgoing to counterparties flagged as "
        "related/affiliated parties."
    )
    numerator_categories: list[str] = Field(
        description="Category slugs for the numerator: "
        "revenue, capex, opex, lease, personnel, utilities, tax, insurance, "
        "interest, marketing, professional. "
        "Empty means 'all transactions' (for ebitda/revenue agg)."
    )
    denominator_agg: AggKind = Field(
        default=AggKind.SUM_OUTFLOW,
        description="How to compute the denominator. Ignored if output_kind=dollar_amount."
    )
    denominator_categories: list[str] = Field(
        default_factory=list,
        description="Category slugs for the denominator. Ignored if output_kind=dollar_amount."
    )
    comparator: str = Field(
        description="'<=' if the value must not EXCEED the threshold. "
        "'>=' if the value must be AT LEAST the threshold."
    )
    is_conditional: bool = Field(
        default=False,
        description="True only if the covenant says it applies ONLY WHEN a precondition is met "
        "(e.g. 'only if financing receipts exceed $X')."
    )
    condition_threshold_dollars: float | None = Field(
        default=None,
        description="Dollar precondition threshold, if is_conditional=True."
    )


SYSTEM_PROMPT = """\
You are a financial covenant analyst. Given a clause from a Kazakh credit \
agreement (in Russian), extract the precise mathematical formula.

## Categories available
revenue, capex, opex, lease, personnel, utilities, tax, insurance, interest, \
marketing, professional

## Key definitions
- EBITDA = Revenue minus Operating expenses (opex + utilities + marketing + \
professional + personnel)
- "Выручка" = revenue (inflows)
- "Капитальные затраты" / "капиталовложения" = capex
- "Операционные расходы" = opex
- "Арендные платежи" = lease
- "Расходы на оплату труда" / "персонал" = personnel
- "Коммунальные расходы" = utilities
- "Налоги" = tax
- "Страховые премии" = insurance
- "Процентные расходы" = interest

## Rules
1. If the covenant compares a DOLLAR AMOUNT to a dollar threshold \
(e.g. "spend must not exceed $4,000,000"), output_kind = "dollar_amount". \
The denominator fields are ignored.
2. If the covenant compares a RATIO to a multiple threshold \
(e.g. "≤ 0.42x" or "≥ 1.20x"), output_kind = "ratio".
3. "не менее" / "at least" / "составляло не менее" → comparator = ">="
4. "не превышал" / "не более" / "must not exceed" → comparator = "<="
5. If the clause says "each individual line" or "каждая отдельная статья" — \
the test is the LARGEST single category total. Use max_single_category \
and list each category. output_kind = "dollar_amount".
6. A springing/conditional covenant (applies only if some precondition holds): \
set is_conditional=True and condition_threshold_dollars.
7. If the formula is "Revenue minus the largest of [Category A] and [Category B]" \
("Выручка за вычетом наибольшей из величин ..."), use \
numerator_agg = "revenue_minus_max_category" and list the categories \
(e.g. ["personnel", "tax"]). output_kind = "dollar_amount".
8. If the covenant tests "assets transferred to unrestricted subsidiaries" \
("активов, переданных неограниченным дочерним организациям") as a fraction \
of something — use numerator_agg = "unrestricted_transfer". Those subsidiaries \
are identified from the KYC security-coverage table, not from ownership.
9. If the covenant tests "financing receipts" / "поступления по финансированию" \
(loan drawdowns, facility proceeds), use numerator_agg = "financing_inflow". \
These are NOT revenue — they are borrowing proceeds.
10. If the covenant tests the SUM of revenue AND financing proceeds \
("суммы выручки и поступлений по финансированию"), use \
numerator_agg = "revenue_plus_financing". This combines both revenue inflows \
and financing inflows (loan drawdowns) into a single numerator.

Respond ONLY with valid JSON matching the schema. No explanation."""


def _build_llm():
    from langchain_deepseek import ChatDeepSeek

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    return ChatDeepSeek(
        model="deepseek-chat",
        api_key=api_key,
        timeout=30,
        max_retries=0,
        temperature=0,
    )


def extract_formula(clause_text: str, *, max_retries: int = 3) -> FormulaSpec | None:
    llm = _build_llm()
    structured = llm.with_structured_output(FormulaSpec)

    for attempt in range(max_retries):
        try:
            result = structured.invoke([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": clause_text},
            ])
            if isinstance(result, FormulaSpec):
                return result
            return FormulaSpec.model_validate(result)
        except Exception as exc:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  [retry {attempt+1}/{max_retries}] {exc.__class__.__name__}: {exc}")
                time.sleep(wait)
            else:
                print(f"  [FAILED after {max_retries} attempts] {exc}")
                return None
    return None


_REVENUE_FINANCING_RE = re.compile(
    r"выручк\w*\s+и\s+поступлен\w+\s+по\s+финансирован", re.I,
)
_UNRESTRICTED_RE = re.compile(r"неограниченн\w+\s+дочерн", re.I)


def _fixup(spec: FormulaSpec, rule_text: str) -> FormulaSpec:
    if _REVENUE_FINANCING_RE.search(rule_text):
        spec.numerator_agg = AggKind.REVENUE_PLUS_FINANCING
        spec.numerator_categories = []
    if _UNRESTRICTED_RE.search(rule_text):
        # Transfers to unrestricted subsidiaries are identified via the KYC
        # pledge-coverage table, not via the related-party ownership table.
        spec.numerator_agg = AggKind.UNRESTRICTED_TRANSFER
        spec.numerator_categories = []
    return spec


def extract_formulas(
    rules: dict[str, dict[str, Rule]],
) -> dict[str, FormulaSpec]:
    from .rules import RuleKind

    results: dict[str, FormulaSpec] = {}
    for scenario_id, clauses in rules.items():
        for clause_id, rule in clauses.items():
            if rule.kind not in (RuleKind.RATIO, RuleKind.UNKNOWN):
                continue
            key = f"{scenario_id}/{clause_id}"
            print(f"LLM: parsing {key} ({rule.heading[:60]})")
            spec = extract_formula(rule.text)
            if spec:
                spec = _fixup(spec, rule.text)
                results[key] = spec
                print(f"  -> {spec.output_kind} {spec.numerator_agg}/{spec.numerator_categories} "
                      f"/ {spec.denominator_agg}/{spec.denominator_categories} "
                      f"{spec.comparator}"
                      f"{' CONDITIONAL' if spec.is_conditional else ''}")
    return results
