"""DeepSeek fallback for ambiguous or previously unseen ledger descriptions."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

from .categorize import Category


class FlowDirection(StrEnum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"


class TransactionCategorySpec(BaseModel):
    category: Category = Field(description="The single best financial category")
    direction: FlowDirection = Field(description="Echo the supplied inflow/outflow direction")
    transaction_nature: str = Field(
        description="Short normalized nature, for example loan_drawdown or equipment_purchase"
    )
    matched_terms: list[str] = Field(
        description="Words or short phrases from the description supporting the category"
    )


@dataclass(frozen=True)
class CategoryRequest:
    key: str
    description: str
    counterparty: str
    direction: FlowDirection


@dataclass(frozen=True)
class CategoryResolutionResult:
    resolution: TransactionCategorySpec | None
    attempts: int
    error: str | None = None


SYSTEM_PROMPT = """\
You classify ledger transaction descriptions for financial covenant calculations. \
Descriptions may be in English, Russian, or Kazakh.

Allowed categories:
- revenue: genuine sales, customer receipts, or service income;
- financing: loan drawdowns, revolver/credit facility proceeds, borrowing receipts;
- contra: refunds, rebates, reversals, returned deposits, credits reversing a cost;
- capex: equipment, construction, modernization, fixed assets;
- opex: ordinary operating costs not covered by a more specific category;
- lease: rent, lease, hire, charter;
- personnel: payroll, salary, staff and employee costs;
- utilities: electricity, water, heating, telecom and communications;
- tax: taxes, VAT, duties and levies;
- insurance: premiums, policies and insurance cover;
- interest: interest expense or interest income; interest income is not a loan drawdown;
- marketing: advertising, campaigns, promotions, branding and marketing materials;
- professional: legal, audit, advisory, consulting, valuation and similar services;
- unknown: only when the description does not contain enough evidence.

Rules:
1. Use the supplied direction. Revenue and financing must be inflows.
2. A positive amount is not automatically revenue. Loan proceeds are financing.
   Financing requires explicit debt, loan, borrowing, bond, or credit-facility evidence. \
   Vendor co-operative funding, reimbursements, and marketing support are not financing.
3. A refund/rebate/reversal is contra even when it names the original expense category.
4. Classify the transaction itself, not the counterparty's industry.
5. Return matched_terms copied from the transaction description.
6. Do not infer facts from scenario IDs, amounts, covenants, or expected answers; none are supplied.

Respond only with JSON matching the schema."""

_FINANCING_EVIDENCE = re.compile(
    r"loan|borrow|debt|credit\s+facility|revolver|revolving\s+facility|"
    r"facility\s+(?:drawdown|proceeds)|"
    r"bond\s+proceeds|financing\s+(?:receipt|proceed)|кредит|қарыз|қаржыландыру",
    re.I,
)


def _category_concurrency() -> int:
    default = 50
    try:
        configured = int(os.getenv("HALYK_CATEGORY_LLM_CONCURRENCY", str(default)))
    except ValueError:
        return default
    return configured if configured > 0 else default


def _validate_resolution(
    resolution: TransactionCategorySpec,
    request: CategoryRequest,
) -> list[str]:
    errors: list[str] = []
    if resolution.direction is not request.direction:
        errors.append(f"direction {resolution.direction} does not match {request.direction}")
    if request.direction is FlowDirection.OUTFLOW and resolution.category in {
        Category.REVENUE,
        Category.FINANCING,
    }:
        errors.append(f"inflow category {resolution.category} is invalid for an outflow")
    if (
        resolution.category is Category.FINANCING
        and _FINANCING_EVIDENCE.search(request.description) is None
    ):
        errors.append("financing requires explicit loan, debt, or credit-facility evidence")
    if resolution.category is not Category.UNKNOWN and not resolution.matched_terms:
        errors.append("matched_terms must support a non-unknown category")
    return errors


async def _resolve_one(
    structured,
    request: CategoryRequest,
    semaphore: asyncio.Semaphore,
    *,
    max_retries: int = 3,
) -> CategoryResolutionResult:
    payload = json.dumps(
        {
            "description": request.description,
            "counterparty": request.counterparty,
            "direction": request.direction,
        },
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": payload},
    ]
    last_error: str | None = None
    validation_feedback = ""
    for attempt in range(max_retries + 1):
        messages[-1]["content"] = payload + validation_feedback
        try:
            async with semaphore:
                result = await structured.ainvoke(messages)
            resolution = (
                result
                if isinstance(result, TransactionCategorySpec)
                else TransactionCategorySpec.model_validate(result)
            )
            errors = _validate_resolution(resolution, request)
            if errors:
                validation_feedback = (
                    "\nThe previous answer was invalid: "
                    + "; ".join(errors)
                    + ". Return a corrected classification."
                )
                raise ValueError("; ".join(errors))
            return CategoryResolutionResult(resolution=resolution, attempts=attempt + 1)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max_retries:
                print(f"  [category retry {attempt + 1}/{max_retries}] {request.key}: {last_error}")
                await asyncio.sleep(2**attempt)
    print(f"  [category FAILED after {max_retries + 1} attempts] {request.key}: {last_error}")
    return CategoryResolutionResult(
        resolution=None,
        attempts=max_retries + 1,
        error=last_error,
    )


async def resolve_categories_async(
    requests: list[CategoryRequest],
) -> dict[str, CategoryResolutionResult]:
    if not requests:
        return {}

    from .llm_extract import _build_llm, _close_llm

    unique: dict[tuple[str, str, FlowDirection], CategoryRequest] = {}
    request_fingerprints: dict[str, tuple[str, str, FlowDirection]] = {}
    for request in requests:
        fingerprint = (
            " ".join(request.description.casefold().split()),
            " ".join(request.counterparty.casefold().split()),
            request.direction,
        )
        unique.setdefault(fingerprint, request)
        request_fingerprints[request.key] = fingerprint
        print(f"Category LLM: queued {request.key} ({request.description[:70]})")

    llm = _build_llm()
    structured = llm.with_structured_output(TransactionCategorySpec)
    semaphore = asyncio.Semaphore(_category_concurrency())
    fingerprints = list(unique)
    try:
        completed = await asyncio.gather(
            *(
                _resolve_one(structured, unique[fingerprint], semaphore)
                for fingerprint in fingerprints
            )
        )
    finally:
        await _close_llm(llm)
    by_fingerprint = dict(zip(fingerprints, completed, strict=True))
    results = {
        key: by_fingerprint[fingerprint] for key, fingerprint in request_fingerprints.items()
    }
    for request in requests:
        result = results[request.key]
        if result.resolution is not None:
            print(
                f"Category LLM: completed {request.key} -> "
                f"{result.resolution.category} ({result.resolution.transaction_nature})"
            )
    return results


def resolve_categories(
    requests: list[CategoryRequest],
) -> dict[str, CategoryResolutionResult]:
    return asyncio.run(resolve_categories_async(requests))
