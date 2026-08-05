"""Human-readable answer report.

The submission file is for the scoring system. This is for a person: one card per
question, showing the verdict, the number, the transaction that proves it, and the
clause it came from — document and page included, so every claim is checkable.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from halyk_covenants.domain import Calculation, CovenantResult, CovenantSpec
from halyk_covenants.storage import DuckDBStore

VERDICT_RU = {
    "complied": "СОБЛЮДЁН",
    "violated": "НАРУШЕН",
    "unknown": "НЕ ОПРЕДЕЛЁН",
}
CONFIDENCE_RU = {
    "high": "высокая",
    "medium": "средняя",
    "low": "низкая",
    "unreliable": "ненадёжно",
}
METRIC_RU = {
    "sum": "сумма",
    "count": "количество",
    "max": "максимум",
    "min": "минимум",
    "avg": "среднее",
    "ratio": "отношение",
    "existence": "наличие",
    "frequency": "частота",
}
WINDOW_RU = {
    "calendar_day": "календарный день",
    "calendar_week": "календарная неделя",
    "calendar_month": "календарный месяц",
    "calendar_quarter": "календарный квартал",
    "calendar_year": "календарный год",
    "rolling_days": "скользящее окно",
    "custom": "заданный период",
    "none": "без ограничения периода",
}


@dataclass
class AnswerCard:
    borrower_id: str
    covenant_id: str
    question: str
    result: CovenantResult
    spec: CovenantSpec | None
    calculation: Calculation | None
    evidence: dict[str, Any] | None
    confidence: str
    confidence_flags: list[str]
    review_objection: str | None


def _fmt_number(value: Decimal | int | None, unit: str | None = None) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        normalized = value.normalize()
        text = f"{normalized:,.2f}".replace(",", " ")
        if normalized == normalized.to_integral_value():
            text = f"{int(normalized):,}".replace(",", " ")
    else:
        text = f"{value:,}".replace(",", " ")
    return f"{text} {unit}".strip() if unit else text


def _rule_text(spec: CovenantSpec | None) -> str:
    if spec is None:
        return "—"
    metric = METRIC_RU.get(spec.metric.metric_type, spec.metric.metric_type)
    window = WINDOW_RU.get(spec.time_window.type, spec.time_window.type) if spec.time_window else ""
    threshold = _fmt_number(spec.condition.threshold, spec.condition.currency)
    parts = [f"{metric} {spec.condition.comparator} {threshold}"]
    if window:
        parts.append(f"за {window}")
    return ", ".join(parts)


def _filters_text(spec: CovenantSpec | None) -> list[str]:
    if spec is None:
        return []
    names = {
        "direction": "направление",
        "currency": "валюта",
        "counterparty_name": "контрагент",
        "counterparty_id": "ID контрагента",
        "purpose": "назначение",
        "amount": "сумма",
    }
    ops = {"eq": "=", "neq": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤",
           "in": "входит в", "not_in": "не входит в",
           "contains": "содержит", "not_contains": "не содержит"}
    values = {"incoming": "входящие", "outgoing": "исходящие"}
    out = []
    for f in spec.transaction_filters:
        value = values.get(str(f.value), str(f.value))
        out.append(f"{names.get(f.field, f.field)} {ops.get(f.operator, f.operator)} {value}")
    return out


class AnswerReportBuilder:
    def __init__(self, store: DuckDBStore) -> None:
        self.store = store

    def build_cards(
        self,
        results: list[CovenantResult],
        specs: dict[str, CovenantSpec],
        questions: dict[tuple[str, str], str],
        confidence: dict[tuple[str, str], dict[str, Any]],
    ) -> list[AnswerCard]:
        cards: list[AnswerCard] = []
        for result in results:
            pair = (result.borrower_id, result.covenant_id)
            spec = specs.get(result.covenant_id)
            conf = confidence.get(pair, {})
            cards.append(
                AnswerCard(
                    borrower_id=result.borrower_id,
                    covenant_id=result.covenant_id,
                    question=questions.get(pair, self._default_question(result, spec)),
                    result=result,
                    spec=spec,
                    calculation=self._load_calculation(result),
                    evidence=self._load_transaction(result.evidence_transaction_id),
                    confidence=conf.get("level", "medium"),
                    confidence_flags=conf.get("flags", []),
                    review_objection=spec.review_objection if spec else None,
                )
            )
        order = {"unreliable": 0, "low": 1, "medium": 2, "high": 3}
        cards.sort(key=lambda c: (order.get(c.confidence, 2), c.borrower_id, c.covenant_id))
        return cards

    @staticmethod
    def _default_question(result: CovenantResult, spec: CovenantSpec | None) -> str:
        return (
            f"Соблюдает ли заёмщик {result.borrower_id} "
            f"ковенант {result.covenant_id}?"
        )

    def _load_calculation(self, result: CovenantResult) -> Calculation | None:
        if not result.calculation_id:
            return None
        row = self.store.connection.execute(
            "SELECT calculation_json FROM calculations "
            "WHERE calculation_id = ? AND borrower_id = ?",
            [result.calculation_id, result.borrower_id],
        ).fetchone()
        return Calculation.model_validate_json(row[0]) if row else None

    def _load_transaction(self, transaction_id: str | None) -> dict[str, Any] | None:
        if not transaction_id:
            return None
        row = self.store.connection.execute(
            "SELECT transaction_id, transaction_date, amount, currency, direction, "
            "counterparty_name, purpose FROM transactions WHERE transaction_id = ?",
            [transaction_id],
        ).fetchone()
        if not row:
            return None
        return {
            "transaction_id": row[0],
            "date": row[1],
            "amount": row[2],
            "currency": row[3],
            "direction": row[4],
            "counterparty_name": row[5],
            "purpose": row[6],
        }


def render_html(cards: list[AnswerCard], evaluation_date: date) -> str:
    e = html.escape
    counts = {"complied": 0, "violated": 0, "unknown": 0}
    for card in cards:
        counts[card.result.verdict] = counts.get(card.result.verdict, 0) + 1
    needs_review = sum(1 for c in cards if c.confidence in {"low", "unreliable"})

    body = []
    for index, card in enumerate(cards, start=1):
        verdict = card.result.verdict
        rule = _rule_text(card.spec)
        filters = _filters_text(card.spec)

        evidence_block = ""
        if card.evidence:
            ev = card.evidence
            evidence_block = f"""
        <div class="block evidence">
          <div class="block-title">Доказательство — транзакция</div>
          <table class="tx">
            <tr><td>ID</td><td><code>{e(str(ev['transaction_id']))}</code></td></tr>
            <tr><td>Дата</td><td>{e(str(ev['date']))}</td></tr>
            <tr><td>Сумма</td><td><b>{_fmt_number(ev['amount'], ev['currency'])}</b></td></tr>
            <tr><td>Направление</td><td>{e({'incoming':'входящая','outgoing':'исходящая'}
                .get(str(ev['direction']), str(ev['direction'] or '—')))}</td></tr>
            <tr><td>Контрагент</td><td>{e(str(ev['counterparty_name'] or '—'))}</td></tr>
            <tr><td>Назначение</td><td>{e(str(ev['purpose'] or '—'))}</td></tr>
          </table>
        </div>"""

        source_block = ""
        if card.spec and card.spec.source:
            src = card.spec.source
            source_block = f"""
        <div class="block source">
          <div class="block-title">Источник — пункт договора</div>
          <div class="cite">{e(card.spec.raw_text)}</div>
          <div class="meta">Документ: <code>{e(str(src.document_id or '—'))}</code>
            &nbsp;·&nbsp; страница {e(str(src.page or '—'))}</div>
        </div>"""

        calc_block = ""
        if card.calculation and card.calculation.sql:
            calc = card.calculation
            calc_block = f"""
        <details class="block calc">
          <summary>Как посчитано — SQL и параметры</summary>
          <pre>{e(calc.sql)}</pre>
          <div class="meta">Параметры:
            <code>{e(", ".join(calc.parameter_summary) or "—")}</code></div>
          <div class="meta">Строк в выборке: <b>{calc.input_row_count}</b></div>
        </details>"""

        warn_block = ""
        if card.review_objection or card.confidence_flags:
            items = []
            if card.review_objection:
                items.append(f"<li>Замечание ревьюера: {e(card.review_objection)}</li>")
            for flag in card.confidence_flags:
                items.append(f"<li>Флаг проверки: <code>{e(flag)}</code></li>")
            warn_block = f"""
        <div class="block warn">
          <div class="block-title">Требует внимания</div>
          <ul>{''.join(items)}</ul>
        </div>"""

        errors_block = ""
        if card.result.errors:
            errors_block = (
                '<div class="block warn"><div class="block-title">Ошибки</div><ul>'
                + "".join(f"<li>{e(x)}</li>" for x in card.result.errors)
                + "</ul></div>"
            )

        body.append(f"""
    <article class="card v-{verdict} c-{card.confidence}">
      <header>
        <div class="idx">#{index}</div>
        <div class="q">{e(card.question)}</div>
        <div class="badges">
          <span class="verdict v-{verdict}">{VERDICT_RU.get(verdict, verdict)}</span>
          <span class="conf c-{card.confidence}">уверенность: {
              CONFIDENCE_RU.get(card.confidence, card.confidence)}</span>
        </div>
      </header>

      <div class="answer">
        <div class="figure">
          <div class="label">Значение</div>
          <div class="value">{_fmt_number(card.result.number, card.result.number_unit)}</div>
        </div>
        <div class="figure">
          <div class="label">Правило</div>
          <div class="value small">{e(rule)}</div>
        </div>
      </div>

      {'<div class="block filters"><div class="block-title">Учтены только операции</div><ul>'
       + ''.join(f'<li>{e(f)}</li>' for f in filters) + '</ul></div>' if filters else ''}
      {evidence_block}
      {source_block}
      {warn_block}
      {errors_block}
      {calc_block}

      <footer>
        <span>заёмщик <code>{e(card.borrower_id)}</code></span>
        <span>ковенант <code>{e(card.covenant_id)}</code></span>
        <span>статус <code>{e(card.result.status)}</code></span>
      </footer>
    </article>""")

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ответы по ковенантам — {evaluation_date}</title>
<style>
  :root {{
    --bg:#f6f7f9; --card:#fff; --line:#e3e6ea; --ink:#1a1d21; --dim:#6b7280;
    --ok:#0f7b3f; --ok-bg:#e8f6ee; --bad:#b42318; --bad-bg:#fdeceb;
    --unk:#8a6d00; --unk-bg:#fdf6e3; --warn:#b45309;
  }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; padding:32px 20px; background:var(--bg); color:var(--ink);
    font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif }}
  .wrap {{ max-width:920px; margin:0 auto }}
  h1 {{ font-size:24px; margin:0 0 4px }}
  .sub {{ color:var(--dim); margin-bottom:24px }}
  .summary {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:28px }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
    padding:12px 18px; min-width:130px }}
  .stat .n {{ font-size:26px; font-weight:700; line-height:1 }}
  .stat .l {{ color:var(--dim); font-size:13px; margin-top:4px }}
  .stat.bad .n {{ color:var(--bad) }} .stat.ok .n {{ color:var(--ok) }}
  .stat.att .n {{ color:var(--warn) }}

  .card {{ background:var(--card); border:1px solid var(--line); border-left:4px solid var(--line);
    border-radius:12px; padding:20px 22px; margin-bottom:18px }}
  .card.v-violated {{ border-left-color:var(--bad) }}
  .card.v-complied {{ border-left-color:var(--ok) }}
  .card.v-unknown  {{ border-left-color:var(--unk) }}

  header {{ display:grid; grid-template-columns:auto 1fr; gap:4px 12px; margin-bottom:16px }}
  .idx {{ grid-row:span 2; color:var(--dim); font-weight:700; font-size:18px }}
  .q {{ font-weight:600; font-size:16px }}
  .badges {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:6px }}
  .verdict {{ font-weight:700; font-size:12px; letter-spacing:.4px;
    padding:3px 10px; border-radius:20px }}
  .verdict.v-violated {{ background:var(--bad-bg); color:var(--bad) }}
  .verdict.v-complied {{ background:var(--ok-bg); color:var(--ok) }}
  .verdict.v-unknown  {{ background:var(--unk-bg); color:var(--unk) }}
  .conf {{ font-size:12px; padding:3px 10px; border-radius:20px;
    background:#eef1f4; color:var(--dim) }}
  .conf.c-low, .conf.c-unreliable {{ background:var(--bad-bg); color:var(--bad); font-weight:600 }}

  .answer {{ display:flex; gap:28px; flex-wrap:wrap; padding:14px 16px; margin-bottom:14px;
    background:#fafbfc; border:1px solid var(--line); border-radius:8px }}
  .figure .label {{ font-size:12px; color:var(--dim); text-transform:uppercase;
    letter-spacing:.5px }}
  .figure .value {{ font-size:24px; font-weight:700; margin-top:2px }}
  .figure .value.small {{ font-size:15px; font-weight:600 }}

  .block {{ margin-top:12px }}
  .block-title {{ font-size:12px; text-transform:uppercase; letter-spacing:.5px;
    color:var(--dim); margin-bottom:6px }}
  .block ul {{ margin:0; padding-left:20px }}
  .cite {{ border-left:3px solid var(--line); padding:8px 12px; background:#fafbfc;
    white-space:pre-wrap; font-size:14px }}
  .meta {{ color:var(--dim); font-size:13px; margin-top:6px }}
  .warn {{ background:var(--bad-bg); border-radius:8px; padding:12px 14px }}
  .warn .block-title {{ color:var(--bad) }}
  table.tx {{ border-collapse:collapse; width:100% }}
  table.tx td {{ padding:5px 8px; border-bottom:1px solid var(--line); font-size:14px }}
  table.tx td:first-child {{ color:var(--dim); width:150px }}
  details.calc summary {{ cursor:pointer; color:var(--dim); font-size:13px }}
  pre {{ background:#1f2429; color:#e6edf3; padding:12px; border-radius:8px;
    overflow-x:auto; font-size:13px; margin:8px 0 }}
  code {{ background:#eef1f4; padding:1px 5px; border-radius:4px; font-size:13px }}
  footer {{ display:flex; gap:18px; flex-wrap:wrap; margin-top:16px; padding-top:12px;
    border-top:1px solid var(--line); color:var(--dim); font-size:13px }}
  @media print {{ body {{ background:#fff }} .card {{ break-inside:avoid }} }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Ответы по ковенантам</h1>
  <div class="sub">Дата оценки: {evaluation_date} &nbsp;·&nbsp; всего вопросов: {len(cards)}</div>

  <div class="summary">
    <div class="stat bad"><div class="n">{counts.get('violated', 0)}</div>
      <div class="l">нарушено</div></div>
    <div class="stat ok"><div class="n">{counts.get('complied', 0)}</div>
      <div class="l">соблюдено</div></div>
    <div class="stat"><div class="n">{counts.get('unknown', 0)}</div>
      <div class="l">не определено</div></div>
    <div class="stat att"><div class="n">{needs_review}</div>
      <div class="l">требуют проверки</div></div>
  </div>
{''.join(body)}
</div>
</body>
</html>"""


def load_confidence(path) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {(e["borrower_id"], e["covenant_id"]): e for e in raw}
