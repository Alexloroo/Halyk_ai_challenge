# Halyk AI Challenge — Covenant Solver
## Команда ML Empire
Гибридный pipeline для автоматического расчёта финансовых ковенантов по ledger и набору PDF-документов. 

## Быстрый запуск

### Требования

- Docker с Compose plugin;
- GNU Make;
- DeepSeek API key.

Создайте `.env` в корне проекта:

```dotenv
DEEPSEEK_API_KEY=your_key
HALYK_PDF_WORKERS=16
```

Положите датасет в `data/raw/`:

```text
data/raw/
├── master_ledger_2025.csv
├── submission_template.json
├── ground_truth.json          # необязателен, только для локальной проверки
├── CASE.ru.md                 # необязателен для pipeline
├── CASE.kz.md                 # необязателен для pipeline
└── documents/
    ├── <opaque-name>.pdf
    ├── <opaque-name>.pdf
    └── ...
```

Запуск:

```bash
# Linux
make run PYTHON=python3

# Windows: GNU Make из Git Bash/MSYS2
make run PYTHON=python
```

Результат:

```text
submission.json
```

Для другого пути к данным:

```bash
make run \
  DATA_DIR=/absolute/path/to/dataset \
  OUTPUT=/absolute/path/submission.json
```

Дополнительные CLI-параметры передаются через `ARGS`:

```bash
make run ARGS="--team my-team --contact-email team@example.com"
```

`make run` собирает Docker-образ и запускает solver в изолированном окружении. В образ уже включены Tesseract и языковые пакеты `rus`, `kaz`, `eng`.

---

## Как работает pipeline

Pipeline строится вокруг одного принципа: **LLM интерпретирует только то, что действительно требует языкового понимания; все деньги, периоды, агрегаты, сравнения и финальный verdict считаются Python-кодом**.

Основной orchestration находится в `src/halyk/run.py`.

### 1. Template задаёт пространство задачи

`submission_template.json` читается первым и определяет:

- какие scenario должны быть обработаны;
- какие clauses должны присутствовать в ответе;
- сколько ячеек необходимо заполнить.

Pipeline не создаёт новые scenario/clauses и не удаляет существующие. Template остаётся единственным source of truth. Если deterministic parser не находит ожидаемый clause, включается валидируемый DeepSeek fallback; если и он не подтверждает правило текстом договора, ячейка всё равно получает best-effort ответ вместо пропуска.

```text
submission_template.json
        ↓
scenario → [6.1, 6.2, 6.3, ...]
```

### 2. Ledger загружается и связывается со scenario

`master_ledger_2025.csv` преобразуется в `LedgerEntry[]`.

На этом этапе:

- scenario извлекается из `txn_id`, например `TXN-P1-0039 → P1`;
- `account_id` связывает ledger с PDF-документами;
- сохраняются повреждённые или отсутствующие amounts как defects вместо тихого удаления;
- знак суммы определяет направление движения денег, но не экономический смысл транзакции.

Положительный inflow **не считается revenue автоматически**: loan drawdown, refund и interest income должны отличаться от операционной выручки.

### 3. Категоризация транзакций: deterministic first, LLM fallback

Каждая ledger description сначала проходит RU/KZ/EN правила в `categorize.py`.

Если формулировка однозначна:

```text
"Insurance premium" → insurance
"Заработная плата"  → personnel
"Коммуналдық төлем" → utilities
```

результат остаётся полностью детерминированным.

Если description новая или неоднозначная, создаётся короткий запрос в DeepSeek через `llm_categorize.py`. Модель получает только данные, необходимые для классификации транзакции: description, counterparty и направление платежа. После ответа выполняется semantic validation, и только затем категория попадает в ledger.

```text
LedgerEntry
    ↓
regex / aliases
    ├── confidence enough ───────────────→ category
    └── ambiguous / unknown → DeepSeek ─→ validated category
```

### 4. PDF загружаются параллельно и при необходимости проходят OCR

Все PDF имеют непрозрачные имена, поэтому pipeline не полагается на filename.

`docs.py` обрабатывает документы **параллельно на уровне PDF** через `ProcessPoolExecutor`. Каждый worker самостоятельно открывает и закрывает свой файл, а итоговый список документов остаётся в детерминированном порядке.

Для каждой страницы сначала используется native PyMuPDF extraction:

```text
PDF page
   ↓
PyMuPDF native text
   ├── текста достаточно → использовать native text
   └── текста мало       → Tesseract OCR (rus+kaz+eng)
```

OCR включается только для страниц, где native text короче заданного порога. Это позволяет одинаково обрабатывать обычные PDF и image-only сканы.

Количество PDF workers задаётся через:

```dotenv
HALYK_PDF_WORKERS=16
```

Основные OCR-настройки:

```text
HALYK_OCR_ENABLED=1
HALYK_OCR_LANGUAGE=rus+kaz+eng
HALYK_OCR_DPI=300
HALYK_OCR_MIN_NATIVE_CHARS=20
```

### 5. Определяется тип и юридическая версия документа

После extraction каждый PDF превращается в `Document` с полями:

```text
Document
├── text
├── kind
├── edition
├── account_ids
├── native_pages
├── ocr_pages
└── ocr_failed_pages
```

Поддерживаемые document kinds:

```text
credit_agreement
audit_notes
kyc
compliance
operations
unknown
```

Pipeline также различает:

```text
current
superseded
draft
unknown
```

Выбор документа основан не на длине текста, а прежде всего на юридической значимости. Training memo, informational-only документы и тексты, явно не создающие обязательств, не должны вытеснять действующий договор только потому, что они длиннее.

Если релевантный account-linked PDF остаётся `unknown`, `llm_documents.py` используется как fallback-классификатор. После LLM-ответа kind/edition снова проходят контролируемую обработку до выбора документа.

### 6. Scenario связывается с правильным agreement, KYC и audit docs

Из ledger строится mapping:

```text
scenario_id → account_id
```

Для каждого scenario выбираются только документы его account:

```text
account
  ├── current credit agreement
  ├── current KYC
  └── actionable current audit notes
```

Это защищает pipeline от смешивания разных счетов, superseded agreements, draft audit reports и вспомогательных subaccounts.

### 7. Audit notes изменяют рабочий ledger до расчёта ковенантов

`audit.py` извлекает только actionable corrections и применяет их к scenario ledger.

Поддерживаются, в частности:

- transaction reclassification;
- исключение операции из периода;
- восстановление missing amount/entry;
- FX rates и конвертация суммы в USD.

```text
raw scenario ledger
       +
current audit notes
       ↓
corrected scenario ledger
```

Таким образом дальнейшая KYC/rule/evaluation логика работает уже с финансово скорректированным набором операций.

### 8. KYC определяет related parties и unrestricted subsidiaries

`parties.py` читает ownership / coverage information из KYC.

Для related-party логики используются нормализованные юридические имена и KYC threshold, после чего соответствующие ledger entries получают флаги.

Отдельно отмечаются переводы в unrestricted subsidiaries: это другой бизнес-смысл и он не смешивается с обычной related-party ownership логикой.

```text
KYC
 ↓
related legal entities / unrestricted entities
 ↓
flags on LedgerEntry
```

### 9. Из действующего agreement извлекаются правила

`rules.py` разбирает clauses из выбранного current agreement.

Детерминированно извлекаются:

- clause id;
- heading и исходный текст;
- период действия;
- threshold;
- comparator;
- известные категории;
- простой тип правила, если его можно определить без LLM.

Поддерживаются русские и казахские конструкции, включая `Пункт 6.1`, `6.1-тармақ`, периоды `с … по …` / `… бастап … дейін` и RU/KZ/EN category aliases.

Если один из clauses, уже перечисленных в `submission_template.json`, не найден deterministic parser, `llm_rules.py` запускает отдельный DeepSeek fallback. Модель не формирует ответ ковенанта: она должна вернуть точные фрагменты heading и правила из выбранного agreement, тип правила, comparator и категории.

LLM-результат принимается только при выполнении всех ограничений:

- clause ID точно совпадает с отсутствующей template-ячейкой;
- heading и полный текст правила дословно присутствуют в agreement;
- evidence содержит marker запрошенного clause;
- threshold повторно извлекается из evidence детерминированным parser;
- comparator не противоречит явной формулировке minimum/maximum;
- используются только поддерживаемые категории.

Модель не может добавить scenario или clause, которых нет в template. После восстановления `Rule` проходит обычный FormulaSpec и deterministic evaluation pipeline. Конкурентность этого fallback задаётся через `HALYK_RULE_LLM_CONCURRENCY` (по умолчанию `20`).

### 10. Сложные формулы интерпретируются DeepSeek в `FormulaSpec`

Regex хорошо извлекает threshold и период, но сложные ковенанты могут описывать формулу естественным языком:

- `interest / EBITDA`;
- `financing inflow / revenue`;
- `revenue - max(personnel, tax)`;
- maximum category total;
- maximum individual transaction;
- conditional/springing covenant;
- unrestricted transfers;
- revenue + financing proceeds.

Для таких clauses DeepSeek возвращает **не ответ ковенанта**, а только структурированное описание вычисления:

```text
FormulaSpec
├── output_kind
├── numerator_agg
├── numerator_categories
├── denominator_agg
├── denominator_categories
├── comparator
├── is_conditional
├── condition_agg
├── condition_categories
└── condition_threshold_dollars
```

Все независимые FormulaSpec-запросы выполняются асинхронно и параллельно. Лимит задаётся через:

```dotenv
HALYK_LLM_CONCURRENCY=50
```

После ответа модели выполняются deterministic fixups и semantic validation: неподдерживаемые категории, конфликт comparator и некорректный тип формулы не должны молча попасть в evaluator.

#### Новые типы ковенантов: capability verifier и GenericFormulaSpec

После обычного FormulaSpec независимый capability-запрос проверяет, действительно ли закрытая схема `AggKind` выражает весь смысл clause. Проверяются операции, numerator/denominator, условия, comparator и необходимые источники данных. Если существующая формула точна, pipeline оставляет прежний быстрый путь без изменения арифметики.

Если правило требует новой математики, DeepSeek строит ограниченное дерево `GenericFormulaSpec`:

```text
constant / metric / sum_inflow / sum_outflow
max_transaction / max_category / count
add / subtract / multiply / divide
min / max / average / abs
```

AST не содержит Python, SQL или произвольных функций. Глубина, количество узлов, arity операторов, comparator и источники метрик валидируются Python-кодом. Затем второй независимый LLM verifier сверяет построенное дерево с дословным clause evidence. Отклонённая или невыразимая формула не маскируется ближайшим `AggKind`: она получает `unsupported_formula` / `generic_formula_rejected` в private readiness, а submission сохраняет обязательный best-effort ответ.

Каждая невалидная capability-попытка сохраняется в `capabilities.json` внутри `attempt_history`: там видны исходный structured response, первая возвращённая `clause_evidence` и точные ошибки локального валидатора. Поэтому успешный retry больше не скрывает причину первого отказа.

Пример нового выражения:

```text
(revenue - capex - tax) / interest
```

представляется деревом `divide(subtract(...), sum_outflow(interest))`, после чего полностью вычисляется deterministic interpreter через `Decimal`.

#### Реестр метрик и значения из документов

Каждый generic-план объявляет `required_metrics` и источник каждой метрики:

```text
ledger: revenue, financing_inflow, EBITDA, related-party outflow, total inflow/outflow
document: cash balance, total/net debt, equity, current assets/liabilities,
          inventory, group CAPEX и новые statement metrics
```

Для document metric модель может только выбрать account-linked current документ и вернуть точные `evidence` и `value_text`. Candidate ID проверяется, evidence должен дословно присутствовать в переданном тексте, scale должен подтверждаться словами `thousand/million/billion` или их RU/KZ аналогами. Число и multiplier применяет Python.

#### Нефинансовые documentary covenants

Ковенанты вроде наличия действующей страховки, обязательного отчёта или согласия банка используют режим `documentary`. DeepSeek ищет точное подтверждающее evidence в account-linked документах, а Python преобразует установленный факт в `actual=1/0` и формирует verdict. Если подтверждение отсутствует, trace явно помечает, что отсутствие нельзя доказать так же строго, как найденную цитату.

#### Последний full-context fallback

Если capability verifier доказал, что `FormulaSpec` не подходит, а `GenericFormulaSpec` невозможно независимо подтвердить, запускается последний fallback только для одной ячейки:

```text
FormulaSpec → GenericFormulaSpec → full-context calculator + verifier
                                      ↓ отказ
                                 COMPLIANT / 0
```

Calculator получает только данные текущего scenario/account: полный current agreement, исправленный ledger, категории и направления, related/unrestricted flags, audit adjustments, KYC, account-linked current документы, найденные document metrics, threshold, comparator и period. В запрос не попадают `ground_truth.json`, scoring, synthetic manifest, ответы или данные других scenarios.

Модель обязана вернуть пошаговую арифметику со ссылками `txn:<id>`, `metric:<name>` и `step:<n>`. Python независимо проверяет принадлежность каждой транзакции scenario/account и периоду, режим знака `signed/magnitude`, отсутствие повторного прямого учёта, валюты, каждый промежуточный результат, дословные document quotes, неизменность threshold/comparator/period и соответствие status рассчитанному actual. Затем отдельный DeepSeek verifier независимо сверяет результат и источники. При расхождении вся пара запросов повторяется один раз; неподтверждённый ответ не принимается.

### 11. Внешние финансовые значения связываются отдельно

Некоторые ковенанты используют значение не из transaction ledger, например group-level CAPEX из consolidated financial statements.

Pipeline сначала пытается найти такой документ детерминированно по borrower/account context. Если подходящих financial statements несколько и прямой match недостаточен, LLM может выбрать **документ**, но само числовое значение извлекается детерминированным кодом.

То есть модель помогает решить entity-linking задачу, но не придумывает финансовый показатель.

### 12. Финальный расчёт детерминирован для FormulaSpec и GenericFormulaSpec

`evaluate.py` получает:

```text
Rule
+ corrected LedgerEntry[]
+ optional FormulaSpec
+ KYC/audit flags
+ optional external audited value
```

и дальше Python:

1. ограничивает ledger периодом ковенанта;
2. выбирает нужные категории и специальные flags;
3. считает агрегаты через `Decimal`;
4. рассчитывает numerator/denominator;
5. проверяет conditional trigger отдельно от tested metric;
6. сравнивает неокруглённый `actual` с threshold;
7. определяет `COMPLIANT` / `BREACH`;
8. ищет допустимый `evidence_txn_id`;

Единственное исключение — описанный выше full-context fallback. Даже там модель не получает безусловного права записать ответ: Python перепроверяет арифметику и источники, после чего требуется совпадение с независимым verifier.
9. округляет `actual` только при сериализации submission.

LLM не получает ledger total и не принимает финальное решение `COMPLIANT/BREACH`.

### 13. Формируется `submission.json` и выполняется readiness-check

Финальный payload сохраняет структуру исходного template:

```json
{
  "answers": {
    "P1": {
      "6.1": {
        "status": "COMPLIANT",
        "actual": 123.45,
        "evidence_txn_id": null
      }
    }
  }
}
```

Дополнительно pipeline собирает `private_readiness` diagnostics: наличие agreements, качество rule/formula coverage, document/OCR issues и другие сигналы, которые помогают заметить деградацию на неизвестном датасете до отправки ответа.

---

## Архитектура data flow

```text
submission_template.json
          │
          ├──────────────→ required scenarios / clauses
          │
master_ledger_2025.csv
          │
          ↓
      LedgerEntry[]
          │
          ├── deterministic categorization
          │       └── DeepSeek fallback for ambiguous descriptions
          │
documents/*.pdf
          │
          ↓
parallel PyMuPDF extraction
          │
          └── selective Tesseract OCR
          │
          ↓
Document(kind, edition, account_ids, text)
          │
          ├── agreement ──→ deterministic Rule extraction
          │                    └── validated DeepSeek fallback for missing template clauses
          │                                      ↓
          │                                  Rule[] ──→ FormulaSpec (when needed)
          ├── KYC ────────→ related / unrestricted flags
          └── audit ──────→ adjustments / missing amounts / FX
                              │
                              ↓
                    corrected scenario ledger
                              │
             Rule + FormulaSpec + ledger
                              │
                              ↓
                   deterministic evaluator
                              │
                              ↓
              status + actual + evidence_txn_id
                              │
                              ↓
                     submission.json
```

## Почему pipeline гибридный

Полностью regex-based подход быстро ломается на новых формулировках . Полностью LLM-based подход, наоборот, хуже контролируется в финансовой арифметике и сложнее отлаживается.

Здесь граница проведена так:

| Задача | Подход |
|---|---|
| CSV parsing, periods, sums, ratios, FX arithmetic | deterministic Python |
| известные transaction categories | deterministic RU/KZ/EN rules |
| неизвестные/неоднозначные transaction descriptions | DeepSeek fallback |
| native PDF text / OCR | PyMuPDF + Tesseract |
| известные document markers | deterministic classifier |
| неизвестный релевантный document type | DeepSeek fallback |
| threshold/comparator/period | deterministic rule parser |
| пропущенный template clause | DeepSeek evidence extraction → deterministic threshold validation |
| сложная естественно-языковая формула | DeepSeek → validated `FormulaSpec` |
| новая математика | capability verifier → allowlisted `GenericFormulaSpec` AST |
| balance-sheet/document metric | evidence-bound document selection → Python Decimal parsing |
| нефинансовый covenant | documentary evidence → deterministic boolean evaluation |
| final actual/status | deterministic evaluator |

Главная идея: **модель преобразует неструктурированный текст в ограниченную структуру, а критическая финансовая логика остаётся воспроизводимой**.

## Контракт входных данных

### Ledger

Минимальные поля `master_ledger_2025.csv`:

```text
txn_id,date,account_id,counterparty,description,amount,currency
```

Основные соглашения:

- расходы имеют отрицательный знак, поступления — положительный;
- `actual` в submission сериализуется положительным значением;
- `account_id` связывает scenario с документами;
- `counterparty` участвует в KYC matching;
- пустые/повреждённые суммы сохраняются как defects;
- положительная сумма сама по себе не означает revenue.

### PDF

Имена файлов могут быть любыми. Идентичность документа определяется его содержимым: account IDs, document kind, edition и authority markers.

### Submission template

`submission_template.json` — source of truth по ожидаемым scenario и clauses. Solver заполняет существующие ячейки и не меняет схему.

### Ground truth

`ground_truth.json` необязателен и **не используется для расчёта**. Если файл присутствует, он применяется только после формирования submission для локальной regression-проверки.

## Основные модули

| Модуль | Ответственность |
|---|---|
| `ledger.py` | CSV → `LedgerEntry`, scenario mapping, defects |
| `categorize.py` | deterministic RU/KZ/EN categorization |
| `llm_categorize.py` | DeepSeek fallback для неоднозначных ledger descriptions |
| `docs.py` | parallel PDF loading, PyMuPDF, OCR, document metadata, authority ranking |
| `llm_documents.py` | fallback document classification и entity linking |
| `rules.py` | clause, period, threshold, comparator, categories |
| `llm_rules.py` | validated fallback для template clauses, пропущенных rule parser |
| `generic_formula.py` | безопасный AST, metric registry и deterministic interpreter |
| `llm_capabilities.py` | capability verification, generic verifier, document metrics/facts |
| `llm_full_context.py` | scenario-scoped calculator/verifier и локальная проверка арифметики |
| `parties.py` | KYC ownership, related-party и unrestricted flags |
| `audit.py` | reclassification, exclusion, missing amount/entry, FX |
| `llm_extract.py` | concurrent DeepSeek parsing → validated `FormulaSpec` |
| `evaluate.py` | deterministic financial calculation и evidence |
| `quality.py` | private-dataset readiness checks |
| `tracing/` | diagnostic snapshots и stage timings |
| `run.py` | end-to-end orchestration |

## Мультиязычность

Pipeline рассчитан на RU/KZ/EN входные данные:

- OCR: `rus+kaz+eng`;
- document markers для русских и казахских договоров;
- `Пункт 6.1` и `6.1-тармақ`;
- периоды `с … по …` и `… бастап … дейін`;
- RU/KZ/EN aliases для revenue, capex, personnel, utilities и других категорий;
- KYC entities: `LLP/JSC/ТОО/АО/ЖШС/АҚ`;
- DeepSeek получает инструкции для интерпретации русских и казахских clauses.

## Fulltrace — кратко

Для диагностики:

```bash
make fulltrace PYTHON=python3
```

или напрямую:

```bash
python3 -m halyk \
  --data-dir "data/raw" \
  --output "submission.json" \
  --trace-dir "trace" \
  --fulltrace
```

`trace/manifest.json` содержит status и `duration_seconds` для стадий pipeline, а trace-папки сохраняют промежуточные данные для debugging. `private_readiness.json` показывает проблемы, важные для запуска на неизвестном датасете. Если присутствует `ground_truth.json`, fulltrace также выполняет локальное сравнение после создания submission.

Для новых ковенантов stage `11_formulas` дополнительно сохраняет:

```text
capabilities.json
generic_formulas.json
generic_verifications.json
document_metrics.json
documentary_facts.json
full_context.json
capability_prompt.txt
generic_verifier_prompt.txt
metric_prompt.txt
documentary_prompt.txt
full_context_calculator_prompt.txt
full_context_verifier_prompt.txt
```

Поэтому `unsupported_formula`, отсутствующая document metric, отклонённое AST или неподтверждённый documentary fact видны даже без `ground_truth.json`.

Для обычного финального запуска trace не требуется.

## Настройки производительности

```dotenv
# Параллельная обработка PDF/OCR
HALYK_PDF_WORKERS=16

# Параллельная интерпретация сложных covenant formulas
HALYK_LLM_CONCURRENCY=50

# Восстановление пропущенных template clauses
HALYK_RULE_LLM_CONCURRENCY=20

# Capability verification, generic verifier и document evidence extraction
HALYK_CAPABILITY_LLM_CONCURRENCY=30

# Последний scenario-scoped calculator/verifier fallback
HALYK_FULL_CONTEXT_LLM_CONCURRENCY=50

# Selective OCR
HALYK_OCR_ENABLED=1
HALYK_OCR_LANGUAGE=rus+kaz+eng
HALYK_OCR_DPI=300
HALYK_OCR_MIN_NATIVE_CHARS=20
```

PDF parallelism и LLM concurrency решают разные bottlenecks: первый ускоряет CPU/native OCR path, второй скрывает network/model latency независимых FormulaSpec-запросов.

## Диагностический запуск без LLM

```bash
make fulltrace ARGS="--no-llm"
```

Deterministic части продолжат работать, но unknown categories/documents и сложные ratio/conditional clauses могут остаться без полноценной интерпретации. Этот режим предназначен для debugging, а не для финального submission.

## Запуск без Docker

Нужны Python 3.12+, Tesseract OCR и языковые данные `rus`, `kaz`, `eng`.

### Linux

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-rus tesseract-ocr-kaz tesseract-ocr-eng

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

После создания `.env`:

```bash
python3 -m halyk \
  --data-dir "data/raw" \
  --output "submission.json"
```

### Windows PowerShell

Установите Tesseract с языками `rus`, `kaz`, `eng` и убедитесь, что executable доступен через `PATH`. При нестандартном расположении language data задайте `TESSDATA_PREFIX`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

$env:DEEPSEEK_API_KEY = "your_key"

python -m halyk `
  --data-dir "data/raw" `
  --output "submission.json"
```

## Локальный stress-набор

В репозитории есть синтетические сценарии для проверки document selection, OCR, KYC, audit corrections, financing conditions, unrestricted subsidiaries, group CAPEX и сложных формул.

Stress fixtures X25–X44 воспроизводимо пересоздаются:

```bash
python3 tools/generate_x25_x44_stress_fixtures.py
```

Они предназначены для regression testing и проверки поведения на новых RU/EN layouts, а не участвуют в логике solver.
