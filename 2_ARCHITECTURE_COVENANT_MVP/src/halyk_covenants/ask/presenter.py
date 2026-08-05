"""Render one answer for a person reading a terminal."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from halyk_covenants.ask.router import Route
from halyk_covenants.domain import Calculation, CovenantResult

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
RED, GREEN, YELLOW, CYAN = "\033[31m", "\033[32m", "\033[33m", "\033[36m"

VERDICT = {
    "complied": (f"{GREEN}СОБЛЮДЁН{RESET}", GREEN),
    "violated": (f"{RED}НАРУШЕН{RESET}", RED),
    "unknown": (f"{YELLOW}НЕ ОПРЕДЕЛЁН{RESET}", YELLOW),
}
CONFIDENCE = {
    "high": f"{GREEN}высокая{RESET}",
    "medium": f"{YELLOW}средняя{RESET}",
    "low": f"{RED}низкая{RESET}",
    "unreliable": f"{RED}ненадёжно{RESET}",
}
METRIC = {
    "sum": "сумма", "count": "количество", "max": "максимум", "min": "минимум",
    "avg": "среднее", "ratio": "отношение", "existence": "наличие", "frequency": "частота",
}
WINDOW = {
    "calendar_day": "календарный день", "calendar_week": "календарная неделя",
    "calendar_month": "календарный месяц", "calendar_quarter": "календарный квартал",
    "calendar_year": "календарный год", "rolling_days": "скользящее окно",
    "custom": "заданный период", "none": "без ограничения периода",
}
FIELDS = {
    "direction": "направление", "currency": "валюта", "amount": "сумма",
    "counterparty_name": "контрагент", "counterparty_id": "ID контрагента",
    "purpose": "назначение",
}
OPS = {
    "eq": "=", "neq": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤",
    "in": "входит в", "not_in": "не входит в",
    "contains": "содержит", "not_contains": "не содержит",
}
VALUES = {"incoming": "входящие", "outgoing": "исходящие"}


def number(value: Decimal | int | None, unit: str | None = None) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        v = value.normalize()
        text = (
            f"{int(v):,}".replace(",", " ")
            if v == v.to_integral_value()
            else f"{v:,.2f}".replace(",", " ")
        )
    else:
        text = f"{value:,}".replace(",", " ")
    return f"{text} {unit}".strip() if unit else text


def render(
    question: str,
    route: Route,
    result: CovenantResult | None,
    calculation: Calculation | None,
    evidence: dict[str, Any] | None,
    confidence: str,
    document_name: str | None = None,
    width: int = 74,
) -> str:
    line = "─" * width
    out: list[str] = ["", f"{BOLD}ВОПРОС{RESET}", f"  {question}", ""]

    if result is None:
        out += [f"{RED}{BOLD}ОТВЕТ НЕ ПОЛУЧЕН{RESET}", ""]
        for problem in route.problems:
            out.append(f"  {RED}•{RESET} {problem}")
        out += ["", f"{BOLD}ЧТО УДАЛОСЬ ОПРЕДЕЛИТЬ{RESET}"]
        for step in route.steps:
            out.append(f"  {step.what:<14} {step.value}  {DIM}({step.how}){RESET}")
        return "\n".join([*out, ""])

    spec = route.covenant
    verdict_text, _ = VERDICT.get(result.verdict, (result.verdict, ""))

    out += [line, f"{BOLD}ОТВЕТ:{RESET}  {BOLD}{verdict_text}{RESET}", line, ""]
    out.append(f"  Значение   {BOLD}{number(result.number, result.number_unit)}{RESET}")

    if spec is not None:
        metric = METRIC.get(spec.metric.metric_type, spec.metric.metric_type)
        threshold = number(spec.condition.threshold, spec.condition.currency)
        rule = f"{metric} {spec.condition.comparator} {threshold}"
        window = ""
        if spec.time_window and spec.time_window.type == "custom":
            window = f"{spec.time_window.start_date} … {spec.time_window.end_date}"
        elif spec.time_window:
            window = WINDOW.get(spec.time_window.type, "")
        out.append(f"  Правило    {rule}" + (f", за {window}" if window else ""))

    if route.period_applied and route.period:
        start, end = route.period
        out.append(f"  Период     {start} … {end}  {DIM}(взят из вопроса){RESET}")
    else:
        out.append(f"  Дата       {route.at_date}")
    out.append("")

    out.append(f"{BOLD}ПОЧЕМУ ИМЕННО ЭТОТ КОВЕНАНТ{RESET}")
    for step in route.steps:
        out.append(f"  {step.what:<14} {step.value}  {DIM}({step.how}){RESET}")
    if route.alternatives:
        others = ", ".join(f"{s.covenant_id} {v:.2f}" for s, v in route.alternatives)
        out.append(f"  {DIM}Другие кандидаты: {others}{RESET}")
    out.append("")

    if spec is not None and spec.transaction_filters:
        out.append(f"{BOLD}УЧТЕНЫ ТОЛЬКО ОПЕРАЦИИ{RESET}")
        for f in spec.transaction_filters:
            value = VALUES.get(str(f.value), str(f.value))
            out.append(
                f"  • {FIELDS.get(f.field, f.field)} "
                f"{OPS.get(f.operator, f.operator)} {value}"
            )
        if calculation is not None:
            out.append(f"  {DIM}Строк в выборке: {calculation.input_row_count}{RESET}")
        out.append("")

    if evidence:
        out.append(f"{BOLD}ДОКАЗАТЕЛЬСТВО — ТРАНЗАКЦИЯ{RESET}")
        out.append(f"  {evidence['transaction_id']}  •  {evidence['date']}  •  "
                   f"{BOLD}{number(evidence['amount'], evidence['currency'])}{RESET}")
        extra = [
            VALUES.get(str(evidence.get("direction")), str(evidence.get("direction") or "")),
            str(evidence.get("counterparty_name") or ""),
            str(evidence.get("purpose") or ""),
        ]
        detail = "  •  ".join(x for x in extra if x)
        if detail:
            out.append(f"  {DIM}{detail}{RESET}")
        out.append("")

    if spec is not None and spec.source:
        out.append(f"{BOLD}ИСТОЧНИК{RESET}")
        shown = document_name or spec.source.document_id
        out.append(f"  {CYAN}{shown}{RESET}, страница {spec.source.page}")
        for text_line in spec.raw_text.strip().splitlines():
            if text_line.strip():
                out.append(f"  {DIM}│{RESET} {text_line.strip()}")
        out.append("")

    out.append(f"{BOLD}ДОВЕРИЕ{RESET}  {CONFIDENCE.get(confidence, confidence)}")
    if spec is not None and spec.spec_trust != "accepted":
        out.append(f"  {DIM}состояние спецификации: {spec.spec_trust}{RESET}")
    if spec is not None and spec.review_objection:
        out.append(f"  {YELLOW}Замечание ревьюера:{RESET} {spec.review_objection}")
    for problem in route.problems:
        out.append(f"  {YELLOW}!{RESET} {problem}")
    for error in result.errors:
        out.append(f"  {RED}!{RESET} {error}")

    if calculation is not None and calculation.sql:
        out += ["", f"{DIM}SQL: {calculation.sql}{RESET}"]
        if calculation.parameter_summary:
            out.append(f"{DIM}параметры: {', '.join(calculation.parameter_summary)}{RESET}")

    return "\n".join([*out, ""])
