## Быстрый запуск

### Требования

- Docker с Compose plugin;
- GNU Make;
- ключ DeepSeek для интерпретации сложных формул.

Создайте `.env` в корне проекта:

```dotenv
DEEPSEEK_API_KEY=your_key
```

Положите датасет в `data/raw/`:

```text
data/raw/
├── master_ledger_2025.csv
├── submission_template.json
├── ground_truth.json          # необязателен
├── CASE.ru.md                 # необязателен для pipeline
├── CASE.kz.md                 # необязателен для pipeline
└── documents/
    ├── <opaque-name>.pdf
    ├── <opaque-name>.pdf
    └── ...
```

Обычный запуск:

```bash
# Linux
make run PYTHON=python3

# Windows (GNU Make из Git Bash/MSYS2)
make run PYTHON=python
```

Результат появится в:

```text
submission.json
```

Запуск с полной трассировкой всех стадий:

```bash
# Linux
make fulltrace PYTHON=python3

# Windows (GNU Make из Git Bash/MSYS2)
make fulltrace PYTHON=python
```

Результаты:

```text
submission.json
trace/
├── manifest.json
├── 01_template/
├── 02_ledger_loaded/
├── ...
├── 13_submission/
└── 14_ground_truth/
```

`make run` и `make fulltrace` сами собирают Docker-образ. В образ уже включены
Tesseract и языковые пакеты `rus`, `kaz`, `eng`.

Переменная `PYTHON` задаёт имя Python-интерпретатора для Makefile. В Linux обычно
используется `PYTHON=python3`, в Windows — `PYTHON=python`. Значение по умолчанию
в Makefile — `python3`.

## Как указать другой путь к данным

Путь можно передать непосредственно Makefile:

```bash
make run DATA_DIR=/absolute/path/to/dataset OUTPUT=/absolute/path/submission.json
```

Для fulltrace:

```bash
make fulltrace \
  DATA_DIR=/absolute/path/to/dataset \
  OUTPUT=/absolute/path/submission.json \
  TRACE_DIR=/absolute/path/trace
```

Дополнительные CLI-аргументы передаются через `ARGS`:

```bash
make run ARGS="--team my-team --contact-email team@example.com"
```

Диагностический запуск без LLM:

```bash
make fulltrace ARGS="--no-llm"
```

В этом режиме простые правила продолжат считаться, однако сложные ratio и
conditional clauses могут получить fallback-интерпретацию.

## Контракт входных данных

### Ledger

`master_ledger_2025.csv` должен содержать:

```text
txn_id,date,account_id,counterparty,description,amount,currency
```

Принятые соглашения:

- scenario извлекается из `txn_id`: `TXN-P1-0039 → P1`;
- расходы имеют отрицательный знак, поступления — положительный;
- `actual` в submission всегда положительный;
- положительная сумма автоматически не считается revenue;
- пустые и повреждённые суммы не удаляются, а получают defect marker;
- `account_id` связывает scenario с документами;
- `counterparty` используется для exact KYC matching.

### PDF-документы

Имена PDF могут быть непрозрачными. Pipeline определяет по содержимому:

- тип документа;
- основной `ACC-*`;
- текущую, устаревшую или draft-редакцию;
- юридическую значимость документа.

Поддерживаемые типы:

```text
credit_agreement
audit_notes
kyc
compliance
operations
unknown
```

Длинный учебный меморандум не получает приоритет над коротким подписанным
договором. Документы вспомогательных субсчетов (`ACC-8819-02`) не смешиваются с
документами основного счёта (`ACC-8819`).

### Submission template

`submission_template.json` является контрактом полноты. Pipeline заполняет только
существующие scenario и clauses, не создаёт новые ячейки и не меняет их имена.

### Ground truth

`ground_truth.json` необязателен и никогда не участвует в расчёте ответа. Он
читается только после формирования submission на стадии `14_ground_truth` для
локальной диагностики.

Если файла нет, стадия помечается `skipped`, а основной pipeline успешно
завершается.

### Синтетический stress-набор X25–X44

В `data/raw` добавлены 20 RU/EN сценариев по четыре PDF на каждый. Набор
проверяет неизвестные document layouts, current/superseded selection, KYC,
audit reclassification, financing conditions, unrestricted subsidiaries,
group capex из consolidated statements и сложные составные формулы.

Набор воспроизводимо пересоздаётся командой:

```bash
python3 tools/generate_x25_x44_stress_fixtures.py
```

Подробная карта документов и ожидаемых результатов находится в
`data/raw/stress_fixture_manifest.json`. Новые сценарии добавляют 80 PDF,
135 ledger rows и 60 submission cells; все 60 clauses требуют FormulaSpec от
DeepSeek, а 20 уникальных descriptions предназначены для category fallback.
Четыре действующих договора (два RU и два EN) являются image-only и проходят
полную цепочку OCR → document LLM → formula LLM.

## Архитектура

```text
submission_template.json ──→ ожидаемые scenario / clauses
                                   │
master_ledger_2025.csv ──→ LedgerEntry[] ──→ regex categorization
                                   │             └─→ DeepSeek fallback
                                   │
documents/*.pdf ──→ PyMuPDF / OCR ─┤
                                   │
                  ┌────────────────┼─────────────────┐
                  │                │                 │
             agreement           KYC              audit
                  │                │                 │
               Rule[]       related parties   adjustments / FX
                  │                │                 │
                  └────────────────┼─────────────────┘
                                   │
                         structured FormulaSpec
                                   │
                         deterministic evaluator
                                   │
                    actual + status + evidence_txn_id
                                   │
                            submission.json
```

Orchestration находится в `src/halyk/run.py` и разделена на 14 наблюдаемых
стадий:

| Стадия | Что сохраняется в fulltrace |
|---|---|
| `01_template` | scenario и ожидаемые clauses |
| `02_ledger_loaded` | исходный ledger и defects |
| `03_ledger_categorized` | категории всех транзакций и линейдж deterministic/LLM решений |
| `04_pymupdf` | текст каждого PDF, native/OCR pages и ошибки OCR |
| `05_documents_classified` | тип, edition, account IDs и lineage LLM fallback |
| `06_account_mapping` | связь scenario → account |
| `07_documents_selected` | выбранные agreement, KYC и audit docs |
| `08_audit_and_fx` | ledger до/после adjustment и FX |
| `09_related_parties` | KYC threshold, holdings и отмеченные txn |
| `10_rules` | извлечённые clauses, thresholds, periods, categories |
| `11_formulas` | prompt и структурированные FormulaSpec |
| `12_evaluation` | scope, aggregates, comparator, basis и evidence trials |
| `13_submission` | финальный JSON |
| `14_ground_truth` | локальное сравнение, если эталон существует |

## Основные модули

| Модуль | Ответственность |
|---|---|
| `ledger.py` | CSV → `LedgerEntry`, scenario mapping и defects |
| `categorize.py` | высокоточная RU/KZ/EN regex-категоризация |
| `llm_categorize.py` | DeepSeek fallback для новых и неоднозначных формулировок ledger |
| `docs.py` | PyMuPDF, OCR, document kind, edition и authority ranking |
| `llm_documents.py` | DeepSeek fallback для релевантных PDF с неизвестным типом |
| `rules.py` | RU/KZ clauses `6.1–6.3`, period, threshold и comparator |
| `parties.py` | KYC ownership threshold и exact legal-name matching |
| `audit.py` | transaction-scoped reclassify, exclude, missing entry и FX |
| `llm_extract.py` | DeepSeek → валидированный `FormulaSpec` |
| `evaluate.py` | детерминированные actual, status и evidence |
| `tracing/` | сериализация каждого промежуточного состояния |
| `run.py` | end-to-end orchestration |

## Поддержка русского и казахского языков

Мультиязычность работает на нескольких уровнях:

1. Tesseract OCR запускается с `rus+kaz+eng`.
2. Document classifier понимает, например:
   - `ДОГОВОР БАНКОВСКОГО ЗАЙМА`;
   - `БАНКТІК ҚАРЫЗ ШАРТЫ`;
   - `ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР`;
   - `ОРЫНДАУ ДАНАСЫ`.
3. Rule parser поддерживает `Пункт 6.1` и `6.1-тармақ`.
4. Периоды распознаются как `с … по …` и `… бастап … дейін`.
5. Категории имеют RU/KZ/EN aliases: revenue/түсім, capex/күрделі шығындар,
   personnel/еңбекақы, utilities/коммуналдық и другие.
6. KYC поддерживает `LLP/JSC/ТОО/АО/ЖШС/АҚ` и казахские threshold-фразы.
7. DeepSeek получает явную инструкцию интерпретировать русские и казахские clauses.

## Fallback-классификация документов

Если PDF связан с account из текущего template, но его тип не распознан
детерминированными маркерами, stage 05 отправляет его текст в DeepSeek. Модель
определяет тип и edition до выбора agreement/KYC/audit документа. Явно
необязательные training memo и документы, которые не создают обязательств,
не могут быть повышены до действующего кредитного договора.

```text
HALYK_DOCUMENT_LLM_CONCURRENCY=20
```

Исходная и итоговая классификация, число попыток и ошибки сохраняются в
`trace/05_documents_classified/decisions.json`.

## Гибридная категоризация ledger

Сначала применяются высокоточные RU/KZ/EN правила. Только новые
или неоднозначные descriptions передаются DeepSeek. Это сохраняет
детерминированные результаты на известных данных и даёт fallback для
незнакомых формулировок приватного датасета.

LLM получает только description, counterparty и направление платежа. Сумма,
scenario, covenant, ground truth и ожидаемый ответ в запрос не входят. Ответ
проходит semantic validation; ошибка одного запроса не отменяет остальные.
Одинаковые транзакции дедуплицируются, а запросы выполняются параллельно.

Лимит параллельных запросов:

```text
HALYK_CATEGORY_LLM_CONCURRENCY=50
```

В fulltrace решение по каждой транзакции записывается в
`trace/03_ledger_categorized/decisions.json`: initial/final category, причина,
LLM status и validation errors.

## OCR

OCR применяется постранично и только тогда, когда native PyMuPDF extraction
вернул меньше заданного числа символов. Это сохраняет скорость для обычных PDF
и позволяет обрабатывать image-only документы.

Настройки:

```text
HALYK_OCR_ENABLED=1
HALYK_OCR_LANGUAGE=rus+kaz+eng
HALYK_OCR_DPI=300
HALYK_OCR_MIN_NATIVE_CHARS=20
```

В `trace/04_pymupdf/index.json` видны:

```text
native_pages
ocr_pages
ocr_failed_pages
ocr_language
ocr_dpi
```

## Формулы и детерминированный расчёт

Для сложных clauses LLM возвращает только структурированную спецификацию:

```text
FormulaSpec
├── output_kind
├── numerator_agg / numerator_categories
├── denominator_agg / denominator_categories
├── comparator
├── is_conditional
├── condition_agg / condition_categories
└── condition_threshold_dollars
```

Поддерживаются revenue, EBITDA, financing inflow, related-party outflow,
unrestricted transfers, largest category, largest transaction и составные
агрегаты.

После этого Python:

1. ограничивает ledger ковенантным периодом;
2. выбирает категории и специальные KYC/audit flags;
3. считает numerator и denominator через `Decimal`;
4. сравнивает неокруглённый actual с threshold;
5. округляет actual только при сериализации submission.

## Evidence

Evidence ищется counterfactual-проверкой:

```text
BREACH
  ↓
удалить один eligible txn
  ↓
пересчитать тот же covenant
  ↓
COMPLIANT
```

Транзакция возвращается только когда она единственная определяет результат.
Lineage сохраняет происхождение изменений: audit reclassification, restored
missing amount, exclusion, FX conversion, KYC relation и unrestricted transfer.

## Запуск без Docker и Make

Для прямого запуска необходимы Python 3.12+, Tesseract OCR и языковые данные
`rus`, `kaz`, `eng`. В отличие от Docker-режима, системные OCR-зависимости нужно
установить самостоятельно.

### Linux

Установите Tesseract, создайте виртуальное окружение и установите проект:

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-rus tesseract-ocr-kaz tesseract-ocr-eng

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

Создайте `.env` с `DEEPSEEK_API_KEY`, затем запустите обычный pipeline:

```bash
python3 -m halyk \
  --data-dir "data/raw" \
  --output "submission.json"
```

Прямой запуск с полной трассировкой:

```bash
python3 -m halyk \
  --data-dir "data/raw" \
  --output "submission.json" \
  --trace-dir "trace" \
  --fulltrace
```

### Windows PowerShell

Установите Tesseract с языками `rus`, `kaz`, `eng` и убедитесь, что каталог с
исполняемым файлом Tesseract доступен через `PATH`. Если языковые данные лежат
не в стандартном месте, задайте `TESSDATA_PREFIX`, указывающий на каталог
`tessdata`.

Создание окружения и установка проекта:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Ключ можно сохранить в `.env` или установить для текущего PowerShell-сеанса:

```powershell
$env:DEEPSEEK_API_KEY = "your_key"
```

Обычный запуск:

```powershell
python -m halyk `
  --data-dir "data/raw" `
  --output "submission.json"
```

Запуск с полной трассировкой:

```powershell
python -m halyk `
  --data-dir "data/raw" `
  --output "submission.json" `
  --trace-dir "trace" `
  --fulltrace
```

Для диагностического запуска без DeepSeek добавьте `--no-llm` к любой прямой
команде.
