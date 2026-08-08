"""LLM-powered clause interpretation via DeepSeek + LangChain.

Regex extracts the clause text and threshold reliably, but cannot parse
the *formula* — which categories go into the numerator vs denominator,
whether revenue is involved, whether it's a minimum or maximum.

One short call per clause, structured output via Pydantic, retry on errors.
"""

from __future__ import annotations

import asyncio
import inspect
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
    MAX_SINGLE_TRANSACTION = "max_single_transaction"
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
        description="How to compute the denominator. Ignored if output_kind=dollar_amount.",
    )
    denominator_categories: list[str] = Field(
        default_factory=list,
        description="Category slugs for the denominator. Ignored if output_kind=dollar_amount.",
    )
    comparator: str = Field(
        description="'<=' if the value must not EXCEED the threshold. "
        "'>=' if the value must be AT LEAST the threshold."
    )
    is_conditional: bool = Field(
        default=False,
        description="True only if the covenant says it applies ONLY WHEN a precondition is met "
        "(e.g. 'only if financing receipts exceed $X').",
    )
    condition_threshold_dollars: float | None = Field(
        default=None, description="Dollar precondition threshold, if is_conditional=True."
    )
    condition_agg: AggKind = Field(
        default=AggKind.SUM_INFLOW,
        description="Aggregate used only to decide whether a conditional covenant is triggered.",
    )
    condition_categories: list[str] = Field(
        default_factory=list,
        description="Category slugs used by condition_agg, independent from the tested numerator.",
    )
    condition_comparator: str = Field(
        default=">", description="Comparison for the trigger, normally '>' for 'exceeds'."
    )


SYSTEM_PROMPT = """\
You are a financial covenant analyst. Given a clause from a Kazakhstan credit \
agreement in Russian or Kazakh, extract the precise mathematical formula.

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
5. "Each category" / "каждая отдельная статья расходов" means the largest \
category total: use max_single_category. "Each individual transaction" / \
"каждая отдельная операция" means the largest ledger transaction: use \
max_single_transaction. output_kind = "dollar_amount".
6. A springing/conditional covenant (applies only if some precondition holds): \
set is_conditional=True, condition_threshold_dollars, condition_agg and \
condition_categories. The condition aggregate is independent of the tested numerator.
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


async def _close_llm(llm) -> None:
    """Close provider clients before the owning asyncio event loop exits."""
    async_client = getattr(llm, "root_async_client", None)
    async_close = getattr(async_client, "close", None)
    if callable(async_close):
        result = async_close()
        if inspect.isawaitable(result):
            await result
    sync_client = getattr(llm, "root_client", None)
    sync_close = getattr(sync_client, "close", None)
    if callable(sync_close):
        sync_close()


def extract_formula(clause_text: str, *, max_retries: int = 3) -> FormulaSpec | None:
    llm = _build_llm()
    structured = llm.with_structured_output(FormulaSpec)

    for attempt in range(max_retries):
        try:
            result = structured.invoke(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": clause_text},
                ]
            )
            if isinstance(result, FormulaSpec):
                return result
            return FormulaSpec.model_validate(result)
        except Exception as exc:
            if attempt < max_retries - 1:
                wait = 2**attempt
                print(f"  [retry {attempt + 1}/{max_retries}] {exc.__class__.__name__}: {exc}")
                time.sleep(wait)
            else:
                print(f"  [FAILED after {max_retries} attempts] {exc}")
                return None
    return None


_REVENUE_FINANCING_RE = re.compile(
    r"выручк\w*\s+и\s+поступлен\w+\s+по\s+финансирован",
    re.I,
)
_UNRESTRICTED_RE = re.compile(r"неограниченн\w+\s+дочерн", re.I)
_SINGLE_TRANSACTION_RE = re.compile(
    r"кажд\w+\s+отдельн\w+\s+операц|(?:largest|each)\s+(?:single|individual)\s+transaction",
    re.I,
)
_FINANCING_CONDITION_RE = re.compile(
    r"(?:только|лишь)\s+(?:если|при условии).*?поступлен\w+\s+по\s+финансирован|"
    r"only\s+if.*?financing\s+(?:receipts|proceeds)",
    re.I | re.S,
)
_EBITDA_DEFINITION_RE = re.compile(
    r"EBITDA\s*(?:=|рассчитывается\s+как|означает).*?(?=(?:[.;\n]|Пункт\s+6\.\d|$))",
    re.I | re.S,
)
_MINIMUM_COMPARATOR_RE = re.compile(
    r"минимальн|не\s+менее|не\s+ниже|не\s+допускать.*?ниже|"
    r"minimum|at\s+least|must\s+not\s+fall\s+below|"
    r"кем\s+емес|төмен\s+емес|ең\s+төменгі",
    re.I | re.S,
)
_MAXIMUM_COMPARATOR_RE = re.compile(
    r"максимальн|не\s+более|не\s+выше|не\s+превыш|"
    r"maximum|must\s+not\s+exceed|not\s+exceed|"
    r"аспау|артық\s+емес|жоғары\s+емес|ең\s+жоғары",
    re.I,
)
_RATIO_MEANING_RE = re.compile(
    r"ratio|коэффициент|отношени|дол[яи]|fraction|proportion|"
    r"арақатынас|қатынас|үлес|рентабельност|покрыти|leverage|intensity",
    re.I,
)


def _fixup(spec: FormulaSpec, rule_text: str) -> FormulaSpec:
    if _REVENUE_FINANCING_RE.search(rule_text):
        spec.numerator_agg = AggKind.REVENUE_PLUS_FINANCING
        spec.numerator_categories = []
    if _UNRESTRICTED_RE.search(rule_text):
        # Transfers to unrestricted subsidiaries are identified via the KYC
        # pledge-coverage table, not via the related-party ownership table.
        spec.numerator_agg = AggKind.UNRESTRICTED_TRANSFER
        spec.numerator_categories = []
    if _SINGLE_TRANSACTION_RE.search(rule_text):
        spec.numerator_agg = AggKind.MAX_SINGLE_TRANSACTION
    if spec.is_conditional and _FINANCING_CONDITION_RE.search(rule_text):
        spec.condition_agg = AggKind.FINANCING_INFLOW
        spec.condition_categories = []
    return spec


def formula_validation_errors(spec: FormulaSpec, rule: Rule) -> list[str]:
    """Find semantic contradictions before a model formula reaches evaluation."""
    from .categorize import Category
    from .rules import PERCENT, RATIO, RuleKind

    errors: list[str] = []
    valid_categories = {category.value for category in Category}
    for field_name, categories in (
        ("numerator_categories", spec.numerator_categories),
        ("denominator_categories", spec.denominator_categories),
        ("condition_categories", spec.condition_categories),
    ):
        invalid = sorted(set(categories) - valid_categories)
        if invalid:
            errors.append(f"{field_name} contains unsupported categories: {invalid}")

    text = f"{rule.heading} {rule.text}"
    expected_comparator = None
    if _MINIMUM_COMPARATOR_RE.search(text):
        expected_comparator = ">="
    elif _MAXIMUM_COMPARATOR_RE.search(text):
        expected_comparator = "<="
    if spec.comparator not in {"<=", ">="}:
        errors.append(f"unsupported comparator: {spec.comparator}")
    elif expected_comparator is not None and spec.comparator != expected_comparator:
        errors.append(
            f"comparator {spec.comparator} contradicts explicit {expected_comparator} wording"
        )

    requires_ratio = rule.kind is RuleKind.RATIO or (
        rule.kind is RuleKind.UNKNOWN
        and _RATIO_MEANING_RE.search(text) is not None
        and (RATIO.search(text) is not None or PERCENT.search(text) is not None)
    )
    if requires_ratio and spec.output_kind is not OutputKind.RATIO:
        errors.append("ratio covenant returned a dollar_amount formula")
    if spec.is_conditional and spec.condition_threshold_dollars is None:
        errors.append("conditional formula has no trigger threshold")
    if spec.condition_comparator not in {">", ">="}:
        errors.append(f"unsupported condition comparator: {spec.condition_comparator}")
    return errors


def _ebitda_definition_categories(text: str) -> list[str]:
    from .rules import CATEGORY_WORDS

    match = _EBITDA_DEFINITION_RE.search(text)
    if not match:
        return []
    found = {
        category.value for pattern, category in CATEGORY_WORDS if pattern.search(match.group(0))
    }
    definition = match.group(0)
    coordinated_terms = {
        "opex": r"операционн\w*|операциялық\w*|operating",
        "utilities": r"коммунальн\w*|коммуналдық\w*|utilities",
        "marketing": r"маркетинг\w*|жарнам\w*|marketing",
        "professional": r"профессиональн\w*|консультац\w*|кәсіби\w*|professional",
        "personnel": r"персонал\w*|оплат\w*\s+труд\w*|еңбекақ\w*|жалақ\w*|personnel",
    }
    found.update(
        category
        for category, pattern in coordinated_terms.items()
        if re.search(pattern, definition, re.I)
    )
    found.discard("revenue")
    return sorted(found)


def apply_formula_context(
    rules: dict[str, dict[str, Rule]],
    formulas: dict[str, FormulaSpec],
) -> dict[str, FormulaSpec]:
    """Propagate scenario-level definitions into clauses that reference them."""
    for scenario_id, clauses in rules.items():
        definition_categories = _ebitda_definition_categories(
            "\n".join(rule.text for rule in clauses.values())
        )
        if not definition_categories:
            continue
        for clause_id in clauses:
            spec = formulas.get(f"{scenario_id}/{clause_id}")
            if spec is None:
                continue
            if spec.numerator_agg == AggKind.EBITDA:
                spec.numerator_categories = list(definition_categories)
            if spec.denominator_agg == AggKind.EBITDA:
                spec.denominator_categories = list(definition_categories)
    return formulas


def _llm_concurrency() -> int:
    value = int(os.getenv("HALYK_LLM_CONCURRENCY", "50"))
    if value < 1:
        raise ValueError("HALYK_LLM_CONCURRENCY must be at least 1")
    return value


async def _extract_formula_async(
    structured,
    rule: Rule,
    semaphore: asyncio.Semaphore,
    *,
    max_retries: int = 3,
) -> FormulaSpec | None:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": rule.text},
    ]
    for attempt in range(max_retries):
        try:
            async with semaphore:
                result = await structured.ainvoke(messages)
            spec = result if isinstance(result, FormulaSpec) else FormulaSpec.model_validate(result)
            spec = _fixup(spec, rule.text)
            errors = formula_validation_errors(spec, rule)
            if errors:
                raise ValueError("; ".join(errors))
            return spec
        except Exception as exc:
            if attempt < max_retries - 1:
                print(f"  [retry {attempt + 1}/{max_retries}] {exc.__class__.__name__}: {exc}")
                await asyncio.sleep(2**attempt)
            else:
                print(f"  [FAILED after {max_retries} attempts] {exc}")
                return None
    return None


async def extract_formulas_async(
    rules: dict[str, dict[str, Rule]],
) -> dict[str, FormulaSpec]:
    from .rules import RuleKind

    pending: list[tuple[str, Rule]] = []
    for scenario_id, clauses in rules.items():
        for clause_id, rule in clauses.items():
            if rule.kind not in (RuleKind.RATIO, RuleKind.UNKNOWN):
                continue
            key = f"{scenario_id}/{clause_id}"
            pending.append((key, rule))
            print(f"LLM: queued {key} ({rule.heading[:60]})")

    if not pending:
        return apply_formula_context(rules, {})

    llm = _build_llm()
    structured = llm.with_structured_output(FormulaSpec)
    semaphore = asyncio.Semaphore(_llm_concurrency())

    async def parse_one(key: str, rule: Rule) -> tuple[str, FormulaSpec | None]:
        spec = await _extract_formula_async(structured, rule, semaphore)
        if spec is None:
            return key, None
        print(
            f"LLM: completed {key} -> {spec.output_kind} "
            f"{spec.numerator_agg}/{spec.numerator_categories} "
            f"/ {spec.denominator_agg}/{spec.denominator_categories} "
            f"{spec.comparator}"
            f"{' CONDITIONAL' if spec.is_conditional else ''}"
        )
        return key, spec

    try:
        completed = await asyncio.gather(*(parse_one(key, rule) for key, rule in pending))
    finally:
        await _close_llm(llm)
    results = {key: spec for key, spec in completed if spec is not None}
    return apply_formula_context(rules, results)


def extract_formulas(
    rules: dict[str, dict[str, Rule]],
) -> dict[str, FormulaSpec]:
    return asyncio.run(extract_formulas_async(rules))
