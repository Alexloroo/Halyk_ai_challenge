# Halyk Covenant Evaluation MVP

Сервис извлекает ковенанты из PDF, превращает их через DeepSeek в строгие правила
`CovenantSpec`, загружает транзакции в DuckDB и рассчитывает результат обычным Python/SQL.
LLM понимает текст договора, но не считает суммы и не назначает итоговый verdict.

## 1. Как пользоваться

### Рекомендуемый сценарий: весь pipeline через Docker

Это основной способ запуска. PDF распознаются локальным PaddleOCR на GPU, DeepSeek используется
только для компиляции текста ковенантов, а результаты остаются в локальной папке `data/`.

#### Шаг 0. Подготовить `.env`

```bash
cp .env.example .env
```

Заполните файл:

```dotenv
DEEPSEEK_API_KEY=your_deepseek_key
LANGSMITH_API_KEY=your_langsmith_key
LANGSMITH_PROJECT=halyk-covenants
LANGSMITH_TRACING=true
```

Обязателен только `DEEPSEEK_API_KEY`. LangSmith можно не настраивать, если удалённые traces пока
не нужны. Docker Compose читает `.env` автоматически — выполнять `source .env` для Docker не
требуется.

#### Шаг 1. Положить входные файлы

```text
data/raw/
├── documents/
│   ├── loan_agreement.pdf
│   ├── amendment.pdf
│   └── scanned_appendix.pdf
└── transactions/
    └── transactions.xlsx
```

`preprocess` рекурсивно обходит `data/raw`, поэтому названия подпапок не принципиальны.

Важное ограничение: каждый `.csv`, `.xlsx` и `.parquet` внутри `data/raw` сейчас считается
источником транзакций. Отдельный `borrowers.csv` класть туда не следует. Справочник заёмщиков
поместите на лист `borrowers` того же XLSX либо держите вне `data/raw`.

#### Шаг 2. Собрать образы

```bash
docker compose --profile ai --profile gpu build preprocess-ai ocr-gpu
```

GPU-образ большой: при первой сборке загружаются CUDA 12.9 и PaddlePaddle. Повторные сборки
используют Docker cache.

#### Шаг 3. Проверить GPU

```bash
docker compose --profile gpu run --rm ocr-gpu
```

Ожидаемый результат:

```json
{
  "cuda_compiled": true,
  "device": "gpu:0",
  "gpu_count": 1
}
```

Если `gpu_count` равен нулю либо Docker не знает runtime `nvidia`, смотрите раздел
«Диагностика» ниже.

#### Шаг 4. Запустить preprocessing с GPU OCR

Для первого запуска используйте новую БД:

```bash
docker compose --profile gpu run --rm --no-deps ocr-gpu \
  preprocess /app/data/raw \
  --db /app/data/duckdb/run-01.duckdb \
  --ocr
```

Локальная папка `./data` смонтирована в контейнер как `/app/data`. Поэтому созданная внутри
контейнера БД появится на хосте как:

```text
data/duckdb/run-01.duckdb
```

Во время выполнения показывается текущий этап:

```text
[preprocess 1/5] transactions.xlsx: started
[preprocess 1/5] transactions.xlsx: completed
[preprocess 3/5] loan_agreement.pdf: started
[compile 1/3] loan_agreement.pdf candidate-...: waiting for DeepSeek
[compile 1/3] loan_agreement.pdf candidate-...: compiled
```

Строка `waiting for DeepSeek` не означает зависание. Один запрос может выполняться десятки
секунд, а неоднозначный covenant может пройти несколько repair-попыток. Точный вызов виден в
LangSmith.

OCR запускается только для страниц без достаточного native text. Обычные текстовые PDF читаются
через PyMuPDF без OCR. На первом scanned PDF Paddle может загрузить модели; дальше они берутся из
Docker volume `paddle-models`.

Итоговый JSON должен содержать:

```json
{
  "loaded_transaction_rows": 20,
  "parsed_documents": 3,
  "detected_candidates": 8,
  "compiled_covenants": 8,
  "failed_compilations": 0,
  "errors": []
}
```

Количество строк и ковенантов зависит от ваших данных. Перед продолжением обязательно проверьте
`failed_compilations` и `errors`.

#### Шаг 5. Посмотреть CovenantSpec

```bash
docker compose --profile ai run --rm --no-deps preprocess-ai \
  inspect-covenants \
  --db /app/data/duckdb/run-01.duckdb
```

Проверьте для каждого правила:

- `borrower_ids`;
- `metric.metric_type` и `field`;
- `transaction_filters` и exclusions;
- comparator и threshold;
- time window и effective dates;
- currency и evidence mode;
- исходный документ и страницу.

Если CovenantSpec неверен, evaluation воспроизводимо посчитает неверно — поэтому это главная
точка ручного контроля после LLM.

#### Шаг 6. Рассчитать все borrower/covenant пары

Подставьте дату, для которой требуется проверка:

```bash
docker compose --profile ai run --rm --no-deps preprocess-ai \
  evaluate-all \
  --at-date 2026-04-30 \
  --db /app/data/duckdb/run-01.duckdb \
  --output /app/data/submissions/internal-results.json
```

Результат сохраняется в `data/submissions/internal-results.json`. Каждая пара считается
независимо: одна ошибка не останавливает остальные результаты.

#### Шаг 7. Сформировать submission

Пока официальный формат не опубликован, используется синтетический профиль
`configs/submission/synthetic.yaml`:

```bash
docker compose --profile ai run --rm --no-deps \
  -v "$PWD/configs:/app/configs:ro" \
  preprocess-ai \
  serialize-submission \
  --results /app/data/submissions/internal-results.json \
  --profile /app/configs/submission/synthetic.yaml \
  --output /app/data/submissions/submission.json
```

Папка `configs/` подключается к контейнеру read-only отдельным volume. Затем проверьте файл
независимой командой:

```bash
docker compose --profile ai run --rm --no-deps \
  -v "$PWD/configs:/app/configs:ro" \
  preprocess-ai \
  validate-submission \
  --submission /app/data/submissions/submission.json \
  --profile /app/configs/submission/synthetic.yaml
```

Готовый ответ находится в `data/submissions/submission.json`.

#### Повторный запуск

Неизменённые файлы пропускаются по SHA-256. Но если изменить уже загруженный файл транзакций и
запустить его в ту же БД, новые строки добавятся к старым. Для независимых экспериментов меняйте
имя базы:

```text
data/duckdb/run-01.duckdb
data/duckdb/run-02.duckdb
data/duckdb/final.duckdb
```

### Формат входных данных

Команда `preprocess` рекурсивно обходит переданную директорию. Удобная раскладка:

```text
data/raw/
├── documents/
│   ├── contract_alpha.pdf
│   └── limits_appendix.pdf
└── transactions/
    ├── transactions.xlsx
    ├── additional_transactions.csv
    └── archive.parquet
```

Поддерживаются:

- документы с ковенантами — `.pdf`;
- транзакции — `.csv`, `.xlsx`, `.parquet`;
- лист `borrowers` внутри Excel — необязательный справочник заёмщиков и алиасов.

В корне входной директории могут быть подпапки с любыми именами. Остальные типы файлов
игнорируются. Старые `.xls` и `.xlsm` в текущем batch-ingestion не поддержаны. Для `.xlsx`
таблица транзакций должна находиться на первом листе.

Минимально необходимые столбцы транзакций:

| Столбец | Назначение | Пример |
|---|---|---|
| `transaction_id` | идентификатор транзакции | `TX-0001` |
| `transaction_date` или `date` | дата операции | `2026-04-10` |
| `amount` | точная сумма | `5000000.00` |

Для полноценной оценки обычно также нужны:

| Столбец | Назначение |
|---|---|
| `borrower_id` | связь транзакции с заёмщиком |
| `account_id` | счёт |
| `currency` | `KZT`, `USD` и т. п. |
| `direction` | `incoming` или `outgoing` |
| `counterparty_id` | ID контрагента |
| `counterparty_name` | имя контрагента |
| `purpose` | назначение платежа |
| `source_row_id` | исходный ID строки, если он уже есть |

Идентификаторы читаются как строки, поэтому ведущие нули сохраняются. Суммы хранятся как
`DECIMAL(38, 6)`, а не `FLOAT`. Валюты автоматически не конвертируются и не складываются.

### Локальная среда для разработки

Локальная установка требует Python 3.12+:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Создайте `.env` на основе примера:

```bash
cp .env.example .env
```

Заполните как минимум:

```dotenv
DEEPSEEK_API_KEY=your_key
LANGSMITH_API_KEY=your_key
LANGSMITH_PROJECT=halyk-covenants
LANGSMITH_TRACING=true
```

Если ваша локальная версия CLI не загружает `.env` автоматически, экспортируйте его перед
запуском:

```bash
set -a
source .env
set +a
```

`DEEPSEEK_API_KEY` нужен для `preprocess`, если во входной папке есть хотя бы один PDF.
Для обработки только CSV/XLSX/Parquet ключ не нужен. LangSmith необязателен: без его ключа
pipeline продолжит работать, но удалённых traces не будет.

Основные настройки находятся в `configs/default.yaml`. Передать другой YAML можно через
`preprocess ... --config path/to/config.yaml`. Отдельные значения переопределяются переменными
с префиксом `HALYK_`, например:

```bash
export HALYK_DEEPSEEK__MODEL=deepseek-v4-pro
export HALYK_OCR__DEVICE=gpu:0
```

### Быстрая проверка на готовой синтетике

Сгенерировать тестовые PDF, XLSX, эталонные ковенанты и Q&A:

```bash
.venv/bin/halyk-covenants generate-synthetic --output data/synthetic
```

Запустить независимый детерминированный benchmark без DeepSeek и OCR:

```bash
.venv/bin/halyk-covenants benchmark \
  --dataset data/synthetic \
  --min-component-accuracy 1.0
```

Либо одной командой пересоздать синтетику и выполнить benchmark:

```bash
.venv/bin/halyk-covenants benchmark-full --output data/synthetic
```

Отчёты появятся здесь:

```text
data/synthetic/benchmark/report.json
data/synthetic/benchmark/report.md
```

### Локальный запуск без OCR

#### Шаг 1. Предобработка

```bash
.venv/bin/halyk-covenants preprocess data/raw \
  --db data/duckdb/hackathon.duckdb
```

Что делает команда:

1. сначала загружает все структурированные файлы и справочник заёмщиков;
2. затем извлекает блоки из PDF;
3. использует только native text; scanned-страницы без `--ocr` не распознаются;
4. находит кандидаты на ковенанты;
5. компилирует их через DeepSeek/LangChain;
6. при неоднозначном ответе запускает ограниченный LangGraph repair-loop;
7. сохраняет транзакции, документы и готовые `CovenantSpec` в DuckDB.

Флаг `--ocr` не заставляет распознавать все страницы. Нормальный текст извлекается напрямую
через PyMuPDF; OCR используется как fallback. Базовая локальная установка `.[dev]` не содержит
Paddle runtime: при `--ocr` CLI теперь сразу сообщит об отсутствующих пакетах до запуска DeepSeek.
Для OCR на RTX 50xx используйте GPU Docker-команду ниже. Без OCR используйте `--no-ocr` или просто
не передавайте флаг.

В конце команда печатает отчёт:

```json
{
  "run_id": "b3495a48-...",
  "scanned_files": 3,
  "loaded_transaction_rows": 20,
  "parsed_documents": 2,
  "detected_candidates": 8,
  "compiled_covenants": 8,
  "failed_compilations": 0,
  "errors": []
}
```

Проверяйте не только код возврата, но и поля `failed_compilations` и `errors`: ошибка отдельного
файла не останавливает остальные файлы.

Файлы кешируются по SHA-256. Повторный запуск с тем же путём и содержимым пропустит их. Если
изменить уже загруженный файл транзакций, текущая реализация добавит его строки в существующую
БД повторно. Поэтому для чистого эксперимента безопаснее использовать новый путь БД:

```bash
.venv/bin/halyk-covenants preprocess data/raw \
  --db data/duckdb/run-2026-08-02.duckdb
```

#### Шаг 2. Проверка скомпилированных правил

```bash
.venv/bin/halyk-covenants inspect-covenants \
  --db data/duckdb/hackathon.duckdb
```

Перед финальным расчётом полезно вручную проверить borrower scope, metric, filters, comparator,
threshold, период, валюту, evidence mode и effective dates.

#### Шаг 3. Оценка всех ковенантов

```bash
.venv/bin/halyk-covenants evaluate-all \
  --at-date 2026-04-30 \
  --db data/duckdb/hackathon.duckdb \
  --output data/submissions/internal-results.json
```

`--at-date` определяет оцениваемую дату и активную версию ковенанта. Каждая пара
`borrower × covenant` считается независимо: ошибка одной пары сохраняется как `failed/unknown`,
но не прерывает весь batch.

Для отладки одной пары без общего registry:

```bash
.venv/bin/halyk-covenants evaluate \
  --transactions data/synthetic/transactions/synthetic_transactions.xlsx \
  --covenant data/synthetic/covenants/COV-ALPHA-MAX.json \
  --borrower-id B001 \
  --at-date 2026-04-30 \
  --db :memory:
```

#### Шаг 4. Формирование submission

Внутренний результат намеренно отличается от конкурсного JSON. Сначала задайте точный профиль
формата в `configs/submission/*.yaml`, затем выполните:

```bash
.venv/bin/halyk-covenants serialize-submission \
  --results data/submissions/internal-results.json \
  --profile configs/submission/synthetic.yaml \
  --output data/submissions/submission.json
```

Профиль управляет именами ключей, verdict labels, наличием evidence, допустимостью `null` и
представлением ratio как доли или процентов.

#### Шаг 5. Независимая валидация submission

```bash
.venv/bin/halyk-covenants validate-submission \
  --submission data/submissions/submission.json \
  --profile configs/submission/synthetic.yaml
```

Код возврата `0` означает валидный JSON, `3` — схема прочитана, но submission не прошёл проверки,
`2` — файл, конфигурация или входные данные некорректны.

### Где появляются результаты

```text
data/
├── duckdb/
│   └── hackathon.duckdb             # транзакции, документы, registry, результаты и audit records
├── submissions/
│   ├── internal-results.json        # богатая внутренняя модель
│   └── submission.json              # строгий внешний формат
└── synthetic/
    ├── documents/                   # тестовые PDF
    ├── transactions/                # тестовый XLSX
    ├── covenants/                   # golden CovenantSpec
    └── benchmark/                   # cases, Q&A и отчёты
```

### Дополнительные Docker-команды

Docker Compose автоматически читает `.env` из корня проекта.

Собрать CPU runtime и проверить синтетику:

```bash
docker compose build
docker compose run --rm generate-synthetic
docker compose run --rm benchmark
```

Запустить preprocessing и evaluation через обычный образ:

```bash
docker compose --profile ai run --rm preprocess-ai
docker compose --profile ai run --rm evaluate-all
```

Контейнер видит локальную папку `./data` как `/app/data`, поэтому входы и результаты остаются на
хосте. Стандартный `preprocess-ai` работает без флага `--ocr`. Чтобы передать собственный путь или
аргументы, команда сервиса переопределяется:

```bash
docker compose --profile ai run --rm --no-deps preprocess-ai \
  preprocess /app/data/raw \
  --db /app/data/duckdb/hackathon.duckdb
```

Для GPU OCR требуется NVIDIA Container Toolkit. Проверка CUDA/Paddle:

```bash
docker compose --profile gpu build ocr-gpu
docker compose --profile gpu run --rm ocr-gpu
```

Полный preprocessing в GPU-образе:

```bash
docker compose --profile gpu run --rm --no-deps ocr-gpu \
  preprocess /app/data/raw \
  --db /app/data/duckdb/run-01.duckdb \
  --ocr
```

Образ использует CUDA 12.9, PaddlePaddle GPU 3.3 и PaddleOCR. Эта версия нужна для Blackwell
`sm_120`, включая RTX 5060/5070/5080/5090; старый cu126 wheel не содержит эту архитектуру. Для
видеокарты с 8 ГБ VRAM задан один GPU, `shm_size: 8gb` и volume для кеша моделей. При CUDA/OOM
ошибке разрешён CPU fallback.

Если Docker пишет `could not select device driver "nvidia"`, настройте runtime:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker info --format '{{json .Runtimes}}'
```

## 2. Короткий гайд по коду

### Главная идея в одном проходе

```text
cli.py: preprocess
    ↓
PreprocessPipeline
    ├── DuckDBStore.load_transactions(...)
    └── PDFIngestor → CovenantDetector → CompilerGraph → CovenantRegistry

cli.py: evaluate-all
    ↓
BatchEvaluationPipeline
    ↓
TemporalResolver → EvaluationService → EvaluatorRegistry → SQL
    ↓
CovenantResult → ResultVerifier → internal-results.json
    ↓
SubmissionSerializer → SubmissionValidator → submission.json
```

### Точка входа: `cli.py`

Файл `src/halyk_covenants/cli.py` связывает команды с прикладными сервисами. В нём нет формул
ковенантов: CLI только читает параметры, создаёт зависимости и печатает/сохраняет результат.

Если нужно понять выполнение конкретной команды, начинайте здесь:

| Команда | Функция в `cli.py` | Следующий основной объект |
|---|---|---|
| `preprocess` | `preprocess_command` | `PreprocessPipeline` |
| `inspect-covenants` | `inspect_covenants_command` | `CovenantRegistry` |
| `evaluate` | `evaluate_command` | `EvaluationService` |
| `evaluate-all` | `evaluate_all_command` | `BatchEvaluationPipeline` |
| `serialize-submission` | `serialize_submission_command` | `SubmissionSerializer` |
| `validate-submission` | `validate_submission_command` | `SubmissionValidator` |
| `benchmark` | `benchmark_command` | `run_benchmark` |

### Что происходит в preprocessing

Главный файл — `src/halyk_covenants/pipeline/preprocess.py`.

`PreprocessPipeline.run()` рекурсивно собирает файлы и намеренно ставит structured data раньше
PDF. Благодаря этому при разборе договора в DuckDB уже есть borrower master и транзакционные ID.

Дальше путь разделяется:

- `storage/duckdb_store.py` читает structured data, сохраняет исходную строку и её hash, затем
  создаёт каноническую `Transaction`;
- `ingestion/pdf.py` открывает PDF через PyMuPDF и обрабатывает каждую страницу;
- `ingestion/quality.py` выбирает native text, layout или OCR route;
- `ocr/paddle.py` выполняет локальное распознавание, если OCR включён;
- `borrowers/resolver.py` сопоставляет найденного заёмщика с ID из registry;
- `covenants/detector.py` ищет текстовые кандидаты с приоритетом на recall;
- `covenants/compiler.py` отправляет clause и semantic context в DeepSeek через LangChain и
  требует JSON, соответствующий Pydantic-схеме;
- `covenants/compiler_graph.py` принимает валидный результат сразу либо запускает до трёх
  repair-попыток LangGraph;
- `covenants/registry.py` сохраняет готовый `CovenantSpec` в DuckDB.

LLM не может незаметно заменить borrower IDs, исходный clause или provenance: после ответа эти
поля повторно накладываются из детерминированно найденного кандидата.

### Центральная модель: `CovenantSpec`

Модели лежат в `src/halyk_covenants/domain/`. Для чтения вычислений важнее всего
`domain/covenant.py`:

```text
CovenantSpec
├── borrower_ids             кому применяется
├── metric                   sum/count/max/min/avg/ratio/existence/frequency
├── transaction_filters      какие строки DuckDB оставить
├── time_window              какой период взять
├── condition                comparator + threshold
├── evidence_mode            нужна ли подтверждающая транзакция
├── effective_from/to        активная версия правила
└── source                   документ, страница и bbox
```

Это граница между вероятностной и детерминированной частью. До `CovenantSpec` система понимает
неидеальный текст. После `CovenantSpec` она выполняет строго заданную программу.

### Как считается результат

Batch начинается в `pipeline/evaluate.py`:

1. `CovenantRegistry.list()` получает все версии правил;
2. `TemporalResolver` выбирает версию, действующую на `--at-date`;
3. `EvaluationService` изолирует ошибку одной borrower/covenant пары;
4. `EvaluatorRegistry` выбирает реализацию по `metric_type`;
5. `sql/builder.py` строит параметризованный `WHERE` из borrower, периода и `FilterSpec`;
6. evaluator выполняет `SUM`, `COUNT`, `MAX` или другую формулу в DuckDB;
7. `evaluators/comparator.py` сравнивает точное число с threshold;
8. evidence selector при необходимости выбирает violating/max/trigger transaction;
9. создаётся `CovenantResult` со статусом `success`, `partial` или `failed`.

Реализации простых агрегатов находятся в `evaluators/aggregate.py`, ratio — в `ratio.py`,
existence — в `existence.py`, frequency — в `frequency.py`. Чтобы добавить новый metric type,
обычно нужно реализовать контракт evaluator и зарегистрировать его в `evaluators/registry.py`.

### Verification и submission

`verification/verifier.py` строит completeness matrix и не позволяет пропустить failed или
отсутствующую пару молча. Проверка отдельной пары умеет повторно получить verdict из number,
comparator и threshold. Сейчас `BatchEvaluationPipeline` автоматически запускает именно batch
completeness-проверку; `verify_pair()` используется отдельно и внутри repair-механизма.

`verification/repair_graph.py` уже содержит ограниченный LangGraph-контур, который разрешает
менять только интерпретацию (`spec`, borrower/period mapping и evidence strategy), после чего
повторяет детерминированный расчёт. Этот граф покрыт отдельным API и тестами, но пока не подключён
автоматически к CLI-команде `evaluate-all`. Поэтому обычный batch сохраняет repairable failure в
отчёте, а не вызывает LLM сам. Непоправимые вычислительные ошибки в любом случае нельзя отдавать
LLM на исправление.

`submission/serializer.py` — единственное место, которое знает внешний формат ответа. Он:

- сортирует пары;
- переводит внутренние verdict labels;
- нормализует `Decimal` в строку;
- при необходимости переводит ratio в проценты;
- добавляет evidence согласно профилю.

`submission/validator.py` затем независимо проверяет готовый объект. При публикации официального
шаблона конкурса менять в первую очередь нужно submission profile, а не evaluator.

### LangSmith traces

Обёртка находится в `observability/tracing.py`. Декоратор `@trace_stage(...)` основан на
LangSmith `@traceable` и используется не только вокруг LLM, но и вокруг PDF parsing, borrower
resolution, preprocessing, evaluation, verification и serialization.

При включённых переменных:

```dotenv
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=halyk-covenants
LANGSMITH_TRACING=true
```

в LangSmith можно открыть корневой trace запуска и провалиться в дочерние этапы. Локальная запись
корневого preprocessing-запуска дополнительно сохраняется в таблице DuckDB
`pipeline_stage_records`. Поля, явно заданные как чувствительные, проходят через redaction перед
отправкой.

### Пример движения одного правила

Текст PDF:

```text
Ежемесячный объём исходящих платежей не должен превышать 15 000 000 KZT.
```

После detector/compiler получается смысловой объект:

```yaml
metric: sum(amount)
filters: direction == outgoing
time_window: calendar_month
condition: value <= 15000000
currency: KZT
evidence_mode: none
```

При `evaluate-all --at-date 2026-04-30` SQL builder ограничивает строки нужным заёмщиком,
апрелем 2026 года, направлением `outgoing` и валютой `KZT`. DuckDB возвращает, например,
`16000000.000000`. Comparator вычисляет `16000000 <= 15000000 → False`, поэтому результат:

```json
{
  "verdict": "violated",
  "number": "16000000.000000",
  "evidence_transaction_id": null,
  "status": "success"
}
```

Evidence равен `null`, потому что нарушение вызвано месячной суммой, а не одной операцией.

### Где менять поведение

| Задача | Основное место |
|---|---|
| Новые имена/форматы колонок | `ingestion/structured.py`, `storage/duckdb_store.py` |
| Другие правила borrower matching | `borrowers/resolver.py` |
| Улучшить поиск clauses | `covenants/detector.py` |
| Изменить prompt/schema compilation | `llm/prompts/compiler.py`, `covenants/compiler.py` |
| Изменить repair loop | `covenants/compiler_graph.py` |
| Добавить метрику | `evaluators/` и `evaluators/registry.py` |
| Изменить период или effective dates | `covenants/temporal.py`, `sql/builder.py` |
| Изменить evidence | `evidence/selectors.py` |
| Подстроить конкурсный JSON | `configs/submission/*.yaml`, `submission/` |
| Изменить tracing/redaction | `observability/tracing.py` |

## Диагностика

Показать все команды и параметры:

```bash
.venv/bin/halyk-covenants --help
.venv/bin/halyk-covenants preprocess --help
```

Типовые причины ошибок:

| Симптом | Что проверить |
|---|---|
| `DEEPSEEK_API_KEY is required` | ключ экспортирован в текущий shell или передан контейнеру |
| `OCR was requested, but ... runtime is missing` | локальная `.venv` не содержит Paddle; используйте `ocr-gpu` profile |
| `Consider using the pymupdf_layout package` | advisory PyMuPDF, не OCR-ошибка; без layout provider table probe не вызывается |
| `failed_compilations > 0` | `inspect-covenants`, LangSmith compiler/repair traces, PDF text |
| `loaded_transaction_rows = 0` | расширение файла и названия обязательных колонок |
| неверный borrower scope | лист `borrowers`, aliases, ID/BIN/IIN в PDF |
| `unknown/failed` | `errors` конкретного `CovenantResult`, валюта, период, metric/filter fields |
| OCR не видит GPU | `ocr-smoke`, NVIDIA runtime, CUDA/Paddle внутри контейнера |
| неожиданно завышенные суммы | не был ли изменён и повторно загружен файл в ту же DuckDB |

Проверки проекта:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
docker compose config --quiet
docker compose --profile gpu config --quiet
```

Live-тесты выключены по умолчанию. Они включаются отдельно переменными
`RUN_DEEPSEEK_LIVE=1`, `RUN_GPU_OCR_LIVE=1` и `RUN_LANGSMITH_LIVE=1`.
