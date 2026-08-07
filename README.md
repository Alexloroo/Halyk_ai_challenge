# Halyk AI Challenge — Covenant Solver

Решение для Halyk AI Challenge: система читает общий реестр транзакций и архив «грязных» PDF-документов, определяет действующие финансовые ковенанты каждого заёмщика, рассчитывает `actual`, выставляет `COMPLIANT` / `BREACH` и при необходимости находит единственную определяющую транзакцию для `evidence_txn_id`.

Текущая реализация специально упрощена под фактический формат challenge-датасета. Основной принцип:

> LLM интерпретирует только сложную формулу ковенанта. Все суммы, коэффициенты, verdict и evidence рассчитываются обычным Python детерминированно.

## 1. Входные данные

По умолчанию проект ожидает датасет в `data/raw/`:

```text
data/raw/
├── CASE.ru.md
├── CASE.kz.md
├── master_ledger_2025.csv
├── submission_template.json
├── ground_truth.json
└── documents/
    ├── <opaque-hash>.pdf
    ├── <opaque-hash>.pdf
    └── ...
```

`HALYK_DATA_DIR` позволяет указать другую директорию:

```bash
export HALYK_DATA_DIR=/path/to/dataset
```

### `master_ledger_2025.csv`

Одна таблица содержит операции всех сценариев:

```text
txn_id,date,account_id,counterparty,description,amount,currency
```

Важные особенности:

- `scenario_id` отсутствует отдельной колонкой и извлекается из `txn_id`;
- `TXN-P1-0039` → `scenario_id = P1`;
- расходы записаны отрицательными суммами, поступления — положительными;
- `actual` в submission всегда положительный;
- `account_id` — счёт заёмщика;
- `counterparty` — вторая сторона конкретной операции, а не владелец счёта;
- часть строк является шумом и относится к account/scenario вне submission template;
- сумма может отсутствовать или быть повреждена — такая строка сохраняется с defect marker.

Пример связи:

```text
scenario P1
    ↓
TXN-P1-xxxx
    ↓
ACC-7801
    ↓
транзакции ACC-7801 с разными counterparties
```

### `documents/`

Имена файлов намеренно непрозрачны. Тип документа, его заёмщик и актуальность определяются только по содержимому.

В архиве встречаются:

- действующие кредитные договоры;
- устаревшие редакции договоров;
- KYC-досье;
- аудиторские примечания;
- compliance / operations документы;
- черновики;
- нерелевантные distractor-документы.

### `submission_template.json`

Шаблон является контрактом полноты. В нём уже перечислены все `scenario_id` и пункты `6.1 / 6.2 / 6.3`, которые требуется заполнить.

Система не создаёт новые ячейки и не переименовывает существующие.

### `ground_truth.json`

Используется только для локальной проверки public dataset. Для формирования submission он не требуется.

---

## 2. Архитектура

Текущий pipeline intentionally flat: один небольшой модуль отвечает за один этап.

```text
submission_template.json
        │
        ├── список scenario_id
        └── список 6.1 / 6.2 / 6.3
        ↓
master_ledger_2025.csv
        ↓
LedgerEntry
        ↓
scenario из txn_id
        ↓
categorize(description)
        ↓
┌──────────────────────── documents/*.pdf ────────────────────────┐
│                                                                  │
│  PyMuPDF text                                                    │
│      ↓                                                           │
│  document kind + edition + account_ids                           │
│      │                                                           │
│      ├── current audit docs → adjustments / exclusions / FX      │
│      ├── current KYC        → related parties                    │
│      └── current agreement  → clauses 6.1 / 6.2 / 6.3            │
│                                      ↓                           │
│                                     Rule                         │
└──────────────────────────────────────┼───────────────────────────┘
                                       │
                     сложные / неоднозначные Rule
                                       ↓
                                    DeepSeek
                                       ↓
                                  FormulaSpec
                                       ↓
                          deterministic evaluation
                                       ↓
                             actual + status
                                       ↓
                         counterfactual evidence
                                       ↓
                               submission.json
```

Вся orchestration-логика находится в `src/halyk/run.py` и разделена концептуально на три стадии:

```text
load → interpret → compute
```

---

## 3. Модули `src/halyk`

```text
src/halyk/
├── __init__.py
├── paths.py
├── ledger.py
├── categorize.py
├── docs.py
├── rules.py
├── parties.py
├── audit.py
├── llm_extract.py
├── evaluate.py
└── run.py
```

| Модуль | Ответственность |
|---|---|
| `paths.py` | Находит `data/raw` или использует `HALYK_DATA_DIR` |
| `ledger.py` | CSV → `LedgerEntry`, извлекает scenario из `txn_id`, хранит defects |
| `categorize.py` | Классифицирует `description` в финансовую категорию |
| `docs.py` | Читает PDF, определяет тип документа, edition и `ACC-*` |
| `rules.py` | Извлекает пункты `6.1–6.3`, period, comparator, threshold, категории |
| `parties.py` | Читает related-party threshold и ownership из KYC |
| `audit.py` | Извлекает reclassification, exclusion, missing entry и FX adjustments |
| `llm_extract.py` | DeepSeek → структурированный `FormulaSpec` для сложных формул |
| `evaluate.py` | Детерминированно считает `actual`, `status` и evidence |
| `run.py` | Собирает весь pipeline и заполняет submission template |

---

## 4. Ledger и scenario mapping

Главная связь с submission задаётся самим `txn_id`:

```python
TXN-P8-0016 → scenario_id = "P8"
```

Для каждого scenario система определяет его `account_id` по ledger:

```text
P8
 ↓
TXN-P8-xxxx
 ↓
ACC-7808
```

После этого `ACC-7808` используется для поиска документов, относящихся к этому заёмщику.

Важно не путать `account_id` и `counterparty`:

```text
ACC-7801
├── TXN → Ashford ...
├── TXN → Bridgeport ...
├── TXN → Foxridge ...
└── TXN → ...
```

Здесь Ashford / Bridgeport / Foxridge — counterparties операций. Владелец `ACC-7801` определяется по authoritative borrower documents, а не по колонке `counterparty`.

---

## 5. Категоризация транзакций

Ledger не содержит готовой колонки категории, поэтому `categorize.py` преобразует свободный `description` в одну из категорий:

```text
revenue
capex
opex
lease
personnel
utilities
tax
insurance
interest
marketing
professional
contra
unknown
```

Сначала распознаются contra-операции: refunds, rebates, credits, returns и reversals.

Это принципиально важно: положительная сумма сама по себе не считается выручкой.

```text
positive amount
    ≠
revenue
```

Например возврат страховой премии или payroll sweep-back остаётся contra-entry и не увеличивает revenue covenant.

---

## 6. Выбор документов

`docs.py` извлекает native text через PyMuPDF и классифицирует документы.

### Типы

```text
credit_agreement
audit_notes
kyc
compliance
operations
unknown
```

### Редакции

```text
current
superseded
draft
unknown
```

Система специально отбрасывает старые договоры с признаками вроде:

```text
НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ
Заменена и изложена ...
```

Для конкретного `account_id` выбирается действующий документ нужного типа. Если подходящих документов несколько, текущая эвристика предпочитает самый содержательный по объёму текста.

---

## 7. KYC и связанные стороны

Related-party covenants нельзя считать просто по всем компаниям из KYC.

`parties.py` извлекает одновременно:

1. список организаций и их voting stake;
2. borrower-specific threshold из самого KYC.

Например:

```text
Company A 34.5%
Company B 18.7%
Company C  6.2%

threshold: 20.0% and above
```

Тогда related party только `Company A`.

Threshold не задан глобально: он может различаться между заёмщиками.

После этого `counterparty` ledger-транзакций нормализуется и сопоставляется с разрешённым набором related parties.

---

## 8. Audit adjustments

Текущие аудиторские документы могут изменять интерпретацию ledger перед covenant calculation.

Поддерживаются:

```text
RECLASSIFY
EXCLUDE
MISSING_ENTRY
NO_CHANGE
FX conversion
```

Примеры:

```text
ledger:
TXN-X классифицирован как OPEX

current audit note:
TXN-X должен быть CAPEX

→ category = CAPEX
```

```text
current audit note:
TXN-X относится к периоду вне covenant window

→ операция исключается из расчёта
```

Если disclosure содержит обязательство, которого нет отдельной строкой ledger, система может добавить synthetic `LedgerEntry` только для расчёта соответствующего covenant.

Также из текущих audit notes может извлекаться EUR→USD rate, после чего релевантные EUR entries конвертируются перед evaluation.

---

## 9. Извлечение правил из кредитного договора

`rules.py` структурно ищет пункты:

```text
Пункт 6.1
Пункт 6.2
Пункт 6.3
```

Для каждого создаётся `Rule`:

```text
Rule
├── scenario_id
├── clause
├── heading
├── text
├── kind
├── comparator
├── threshold
├── period
└── categories
```

Основные типы правил:

```text
MIN_REVENUE
MAX_CATEGORY_SPEND
MAX_RELATED_PARTY
RELATED_PARTY_SHARE
RATIO
UNKNOWN
```

Порог, период и простые категории извлекаются детерминированно regex-правилами.

---

## 10. Где используется DeepSeek

LLM не получает весь датасет и не считает транзакции.

DeepSeek используется только для сложных `RATIO` / `UNKNOWN` clauses, где требуется понять математическую структуру правила.

На выходе модель обязана вернуть структурированный `FormulaSpec`:

```text
FormulaSpec
├── output_kind
├── numerator_agg
├── numerator_categories
├── denominator_agg
├── denominator_categories
├── comparator
├── is_conditional
└── condition_threshold_dollars
```

Поддерживаемые агрегаты включают:

```text
sum_outflow
sum_inflow
financing_inflow
revenue_plus_financing
revenue
ebitda
max_single_category
revenue_minus_max_category
related_party_outflow
```

После получения `FormulaSpec` LLM больше не участвует в расчёте.

---

## 11. Deterministic evaluation

`evaluate.py` выполняет расчёт обычным Python.

Общая схема:

```text
Rule
+
FormulaSpec (если нужен)
+
scenario LedgerEntry[]
        ↓
period filter
        ↓
category / related-party selection
        ↓
aggregation
        ↓
actual
        ↓
comparator + threshold
        ↓
COMPLIANT / BREACH
```

`actual` всегда приводится к положительному значению и округляется до двух знаков только при сериализации submission.

---

## 12. Evidence transaction

По правилам challenge `evidence_txn_id` — не просто самая крупная или последняя транзакция.

Текущая реализация ищет evidence counterfactual способом:

```text
исходный результат = BREACH
        ↓
убрать одну использованную транзакцию
        ↓
пересчитать covenant
        ↓
если BREACH → COMPLIANT
        ↓
эта txn является deciding candidate
```

`evidence_txn_id` возвращается только если такая определяющая транзакция ровно одна.

Для агрегатных и коэффициентных тестов evidence обычно остаётся `null`.

---

## 13. Полный run

### Установка

Требуется Python 3.12+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Для LLM-интерпретации сложных формул задайте ключ:

```bash
export DEEPSEEK_API_KEY=your_key
```

Можно также положить его в `.env`:

```dotenv
DEEPSEEK_API_KEY=your_key
```

### Запуск из Python

Текущий `main` предоставляет orchestration через `halyk.run`:

```python
import json
from pathlib import Path

from halyk.run import solve, to_submission
from halyk.paths import template_json

report = solve(use_llm=True)
submission = to_submission(
    report,
    template_json(),
    team="your-team-name",
    contact_email="you@example.com",
    model="deepseek-chat",
)

output = Path("submission.json")
output.write_text(
    json.dumps(submission, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print(f"written: {output}")
```

Для детерминированного debug-run без LLM:

```python
report = solve(use_llm=False)
```

### Запуск из CLI и полная трассировка

Обычный запуск не создаёт и не изменяет каталог `trace/`:

```bash
make run
```

Полная трассировка каждого шага включается отдельной целью Makefile:

```bash
make fulltrace
```

Для детерминированного запуска без обращения к DeepSeek:

```bash
make fulltrace ARGS=--no-llm
```

Эквивалентный прямой вызов:

```bash
python -m halyk --data-dir data/raw --output submission.json --fulltrace
```

При `--fulltrace` существующий `trace/` полностью пересоздаётся. Внутри находятся
нумерованные каталоги `01_template` … `13_submission`, а `manifest.json` содержит
порядок шагов, статусы и ссылки на все артефакты. В `04_pymupdf/` сохраняется
отдельный `.txt` для каждого PDF; табличные состояния записываются в CSV, а
извлечённые правила, формулы и расчёты — в JSON.

В целях безопасности автоматически очищается только каталог, созданный предыдущим
fulltrace-запуском и содержащий ownership-marker в `manifest.json`. Если указать
чужой непустой каталог через `--trace-dir`, запуск завершится ошибкой без удаления
его содержимого.

В этом режиме простые правила продолжают считаться, но сложные ratio/unknown clauses могут быть интерпретированы неточно.

---

## 14. Формат результата

Финальный файл повторяет `submission_template.json`:

```json
{
  "team": "your-team-name",
  "contact_email": "you@example.com",
  "model": "deepseek-chat",
  "answers": {
    "P1": {
      "6.1": {
        "status": "COMPLIANT",
        "actual": 1.23,
        "evidence_txn_id": null
      }
    }
  }
}
```

Для каждой готовой ячейки заполняются только:

```text
status
actual
evidence_txn_id
```

Если правило не удалось извлечь, `run.py` всё равно заполняет ячейку best-effort fallback-ответом, потому что по условиям challenge пустая ячейка не выгоднее неправильной.

---

## 15. Текущие ограничения

Проект сейчас является competition-oriented solver под фактическую структуру challenge dataset, а не универсальной covenant platform.

Основные ограничения текущего `main`:

- PDF читаются через native PyMuPDF text extraction; OCR fallback в `src/halyk` пока отсутствует;
- document classification построен на текстовых маркерах и regex;
- transaction categorization также в основном deterministic / regex-based;
- сложные формулы зависят от корректной интерпретации DeepSeek;
- выбор нескольких подходящих документов одного типа использует простую эвристику по объёму текста;
- public `ground_truth.json` предназначен только для локальной оценки и не является частью production input.

Главная граница системы остаётся простой:

```text
LLM → понять формулу
Python → посчитать и доказать результат
```
