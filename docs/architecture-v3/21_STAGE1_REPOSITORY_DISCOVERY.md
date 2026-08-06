# STAGE 1 — REPOSITORY DISCOVERY

> Ничего не изменено. Только чтение.
> Дата: 2026-08-06. Ветка `covenant-architecture-v3`.
> Данные разобраны в [20_DISCOVERY.md](20_DISCOVERY.md); здесь — репозиторий.

---

## 0. Определённый стек

| Слой | Технология | Признак |
| --- | --- | --- |
| Язык | Python ≥3.12 | `requires-python`, `target-version = "py312"` |
| Модели данных | pydantic 2 + pydantic-settings | `extra="forbid"` повсеместно |
| Хранилище | **DuckDB** (встраиваемая, файл) | `duckdb>=1.1`, 15 таблиц |
| LLM | **DeepSeek** через LangChain | `langchain-deepseek`, `deepseek-v4-pro` |
| Оркестрация | **LangGraph** `StateGraph` | 3 графа |
| Наблюдаемость | **LangSmith** | `langsmith.traceable` через обёртку |
| CLI | Typer | 2 точки входа, 14 команд |
| PDF | PyMuPDF (`fitz`) | обязательная |
| OCR | PaddleOCR 3.4.1 | **опционально**, extra `ocr` |
| Семантика | sentence-transformers + faiss | **опционально**, extra `semantic` |
| Лексический поиск | rank-bm25 | обязательная |
| Нечёткие имена | rapidfuzz | обязательная |
| Табличные данные | pandas <3, pyarrow, openpyxl | обязательные |
| Генерация PDF | reportlab | только для синтетики |
| Тесты / стиль | pytest, ruff | extra `dev` |
| Упаковка | hatchling | — |
| Развёртывание | Docker multi-stage + compose (профили `ai`, `gpu`, `test`) | `python:3.12-slim` |
| CI | GitHub Actions | `codex-1-ci.yml`, ветки `codex-1`/`codex-2` |

**Lock-файлов нет.** Ни `uv.lock`, ни `poetry.lock`, ни `requirements.txt`. Воспроизводимость
держится только на диапазонах версий в `pyproject.toml` — риск подтверждён на практике: свежая
установка притянула `pandas 3.0.5`, несовместимую с окружением.

### Объём

```
src/halyk_covenants   22 пакета,  ~11 000 строк
tests                 67 файлов,    6 136 строк
scripts                5 файлов
TODO / FIXME / HACK    0
```

---

## 1. ENTRYPOINT MAP

### 1.1 Объявленные в `pyproject.toml`

| Точка входа | Модуль | Команд |
| --- | --- | --- |
| `halyk-covenants` | `halyk_covenants.cli:app` | 13 |
| `halyk-review` | `halyk_covenants.review_cli:app` | 1 |

### 1.2 Команды `halyk-covenants`

| Команда | Назначение | LLM | Актуальность |
| --- | --- | --- | --- |
| `preprocess` | архив → блоки → кандидаты → компиляция → ревью | **да** | ядро |
| `evaluate-all` | реестр ковенантов → результаты | нет | ядро |
| `run` | `preprocess` + `evaluate-all` + отчёт | да | обёртка |
| `inspect-covenants` | печать скомпилированных спецификаций | нет | отладка |
| `evaluate` | одна пара заёмщик/ковенант из JSON-файла | нет | отладка |
| `serialize-submission` | результаты → формат ответа | нет | **формат не тот** |
| `validate-submission` | проверка файла ответа | нет | **формат не тот** |
| `ask` | свободный вопрос → ответ в терминал | нет | **не нужна** |
| `answer-report` | результаты → HTML | нет | **не нужна** |
| `generate-synthetic` | генерация mock-датасета | нет | **mock** |
| `benchmark` | прогон по синтетическому эталону | нет | **mock** |
| `benchmark-full` | генерация + прогон | нет | **mock** |
| `ocr-smoke` | проверка CUDA для Paddle | нет | инфраструктура |

### 1.3 Прочие исполняемые входы

```
scripts/cloud1_demo.py       офлайн-демо на синтетике        mock
scripts/evaluate.py          ?                                проверить
scripts/regression_v2.py     регрессия по синтетике           mock
scripts/docker-healthcheck.sh  healthcheck контейнера         инфраструктура
scripts/ocr-healthcheck.sh     healthcheck GPU-контейнера     инфраструктура
ask.bat / ask.sh / ../ask.bat  лаунчеры для ask               не нужны
run.bat / ../run.bat           лаунчеры для run               обёртка
docker-compose.yml services: generate-synthetic, benchmark, preprocess-ai,
                             evaluate-all, ocr-gpu, test
```

---

## 2. DATA FLOW MAP

### 2.1 Фактический поток (как построено)

```
                    ┌─ ФАЗА 1: ПРЕДОБРАБОТКА (единственная с LLM) ─┐
data/raw/
  transactions/*.csv,xlsx ──► storage.load_transactions
                                  │  нормализация direction, валют
                                  ▼
                            raw_transactions ──► transactions
                                                 borrowers
                                                 borrower_aliases
                                                 borrower_identifiers
  documents/*.pdf ──► ingestion.PDFIngestor
                          │ PageQualityRouter: native | layout | ocr | failed
                          ▼
                      DocumentBlock[] ──► document_blocks
                          │
                          ▼
                    borrowers.BorrowerResolver          (аннотация области)
                          │
                          ▼
                  covenants.CovenantDetector            (регулярки EN/RU/KZ)
                          │  CovenantCandidate[]
                          ▼
                  documents.HybridRetriever             (BM25 + опц. эмбеддинги)
                          │  document_context
                          ▼
                  covenants.CompilerGraph               ◄── LLM #1..#3
                    compile → validate → repair
                          │  CovenantSpec
                          ▼
                  review.SpecReviewGraph                ◄── LLM #4..#7
                    review → grade_context →
                    expand_retrieval → recompile
                          │  CovenantSpec + spec_trust
                          ▼
                  covenants.CovenantRegistry ──► covenants, covenant_borrowers

                    ┌─ ФАЗА 2: ОЦЕНКА (LLM не участвует) ─┐
                  CovenantRegistry.list()
                          │
                          ▼
              verification.ManifestBuilder ──► expectation_manifest
                          │
                          ▼
              evaluators.TemporalEvaluationService
                    │ версии по effective_from/to
                    ▼
              evaluators.EvaluationService
                    │ sql.build_where_clause  (закрытый каталог полей)
                    │ Sum|Count|Max|Min|Avg|Ratio|Frequency Evaluator
                    ▼
              DuckDB SQL ──► число
                    │
                    ├─► evaluators.compare() ──► verdict
                    ├─► evidence.selectors ──► evidence.validation
                    └─► Calculation ──► calculations
                          │
                          ▼
              CovenantResult ──► covenant_results, covenant_result_history
                          │
                          ├─► verification.ResultVerifier   (пара + полнота)
                          ├─► verification.DualPathVerifier (не подключён)
                          ├─► verification.confidence ──► confidence.json
                          ├─► reporting.AnswerReportBuilder ──► answers.html
                          └─► submission.SubmissionSerializer ──► submission.json

                    ┌─ ФАЗА 3: РЕВЬЮ ОТВЕТОВ (тупик, codex-2) ─┐
              BatchEvaluationReport ──► pipeline.ReviewPipeline ◄── LLM
                          │  review.ReviewService (клетка _validate_decision)
                          ▼
                    reviewed-results.json          потребителя нет
```

### 2.2 Побочный поток

```
synthetic.generate_synthetic_dataset ──► data/synthetic/{documents,transactions,covenants,benchmark}
                                              │
                                              ▼
                                      benchmark.run_benchmark ──► report.json/md
```

---

## 3. DEPENDENCY MAP

Внутренние зависимости пакетов (ациклические, проверено):

```
domain            ──► (ничего)                     ← фундамент
observability     ──► domain
sql               ──► domain
llm               ──► config, observability
ingestion         ──► domain, observability
ocr               ──► domain, observability
vlm               ──► domain, observability, ocr
borrowers         ──► domain, observability
storage           ──► borrowers, domain, ingestion, observability
documents         ──► domain, observability, storage
evidence          ──► domain, observability, storage
evaluators        ──► domain, evidence, observability, sql, storage
covenants         ──► domain, llm, observability, storage
verification      ──► covenants, domain, evaluators, observability, storage
review            ──► covenants, documents, domain, evaluators, llm, observability, storage
submission        ──► domain, observability
reporting         ──► domain, storage
ask               ──► borrowers, domain
evals             ──► domain
benchmark         ──► domain, evaluators, storage, synthetic
synthetic         ──► covenants, domain, evals, evaluators, pipeline, storage, submission
pipeline          ──► borrowers, covenants, documents, domain, evaluators,
                      ingestion, observability, review, storage, verification   ← хаб
```

**Наблюдения.**

1. `pipeline` — узел с наибольшей связностью (10 пакетов). Любая смена контракта бьёт по нему.
2. `synthetic` (2 050 строк, крупнейший пакет) зависит от `pipeline` — то есть **mock-слой врос в
   production-граф**, а не лежит сбоку.
3. `domain` изолирован — хороший признак, замена моделей не потянет циклы.
4. Один цикл на уровне модулей уже был и обойдён `TYPE_CHECKING`:
   `covenants → llm.prompts → review → covenants.compiler_graph`.

---

## 4. CURRENT STATE MAP

Формат: RESPONSIBILITY / INPUT / OUTPUT / DEPENDENCIES / DATA CONTRACT / MOCK ASSUMPTIONS /
PRODUCTION RELEVANCE / CONFIDENCE.

### C-01 `domain` — модели предметной области

- **RESP** Определяет `CovenantSpec`, `CovenantResult`, `Transaction`, `Borrower`, `Calculation`,
  `DocumentBlock`, `FilterSpec`, `MetricSpec`, `TimeWindowSpec`, `SourceRef`, `FailureStage`.
- **IN** — · **OUT** типы для всех остальных пакетов · **DEPS** нет
- **CONTRACT** pydantic, `extra="forbid"`; `FilterSpec.field` ограничен `FILTER_FIELDS`
- **MOCK** `Transaction` содержит `direction`, `counterparty_id`, `purpose` — колонки
  синтетического набора. `CovenantResult.verdict ∈ {complied, violated, unknown}`.
  `covenant_id` — свободная строка.
- **PROD** **Низкая.** Реальный реестр не имеет `direction`; требуемый словарь —
  `COMPLIANT`/`BREACH`; ключ ячейки — номер пункта.
- **CONF** Высокая (прочитано целиком)

### C-02 `domain/transaction_fields` — закрытый каталог полей

- **RESP** Белый список полей, допустимых в фильтрах и SQL; граница SQL-инъекций.
- **CONTRACT** `PHYSICAL_TRANSACTION_FIELDS ∪ DERIVED_TRANSACTION_FIELD_SQL = FILTER_FIELDS`
- **MOCK** Перечисляет ровно колонки `synthetic_transactions.xlsx`.
- **PROD** **Механизм — да, содержимое — нет.** Реальные ковенанты требуют *категорий*
  (капзатраты, выручка, аренда), которых нет ни в одной колонке.
- **CONF** Высокая

### C-03 `storage` — DuckDB

- **RESP** Схема из 15 таблиц, загрузка структурированных файлов, нормализация.
- **IN** CSV/XLSX/Parquet · **OUT** `transactions`, `borrowers`, `borrower_aliases`, …
- **CONTRACT** `CANONICAL_COLUMNS` из 11 полей; `REQUIRED_COLUMNS = (transaction_id,
  transaction_date, amount)`; `_DIRECTION_ALIASES` приводит `in/out/входящий/…` к
  `incoming/outgoing`
- **MOCK** Предполагает наличие колонки направления и отдельного справочника заёмщиков.
- **PROD** **Средняя.** DuckDB и провенанс `raw_transactions` пригодны; маппинг колонок и
  нормализация направления — нет.
- **CONF** Высокая

### C-04 `ingestion` — разбор PDF

- **RESP** PDF → `DocumentBlock[]`; маршрутизация страниц по качеству.
- **CONTRACT** `PageQualityRouter.classify → native | layout | ocr | failed`;
  `document_id = sha256(содержимое)`
- **MOCK** Синтетика содержала намеренный скан → OCR считался обязательным путём.
- **PROD** **Высокая для native, низкая для OCR.** 199 из 200 реальных PDF имеют текстовый слой.
- **CONF** Высокая

### C-05 `ocr` + `vlm` — PaddleOCR и разбор вёрстки

- **RESP** Распознавание сканов и таблиц на GPU.
- **DEPS** extra `ocr`, `Dockerfile.ocr`, профиль compose `gpu`, `ocr-healthcheck.sh`
- **MOCK** Существуют ради одного синтетического скана.
- **PROD** **Очень низкая.** Один реальный файл без текстового слоя из 200.
- **CONF** Средняя (не проверял, что именно за файл)

### C-06 `borrowers` — разрешение заёмщика

- **RESP** Имя/идентификатор → `borrower_id`; точное → алиас → нечёткое (rapidfuzz, порог 85).
- **CONTRACT** `BorrowerClaim → BorrowerResolution{status, borrower_ids, candidates}`
- **MOCK** Предполагает существование таблицы `borrowers` из `borrowers.csv`.
- **PROD** **Механизм — да, источник — нет.** Реального справочника нет: связь
  `account_id → scenario_id` выводится из префикса `txn_id`, названия компаний — только внутри
  документов. Отказ при неоднозначности — правильное поведение, стоит сохранить.
- **CONF** Высокая

### C-07 `documents` — гибридный поиск

- **RESP** BM25 + опциональные эмбеддинги по блокам.
- **CONTRACT** `HybridRetriever(embedder=None)` → веса `1.0/0.0`, то есть чистый BM25
- **MOCK** Небольшой корпус, ковенанты рассыпаны по документам.
- **PROD** **Средняя.** 843 страницы — поиск нужен. Но задача сместилась: не «найти ковенант
  в шуме», а «выбрать действующую редакцию среди приманок».
- **CONF** Высокая

### C-08 `covenants` — обнаружение, компиляция, реестр

- **RESP** Детектор (регулярки), `CompilerGraph` (LangGraph compile→validate→repair),
  `CovenantRegistry`, детерминированная идентичность, семантическая валидация.
- **IN** `DocumentBlock[]`, `document_context` · **OUT** `CovenantSpec`
- **CONTRACT** `CovenantCandidate{candidate_id, raw_text, borrower_ids, source, confidence}`
- **MOCK** Синтетика содержала явные коды `[COV-ALPHA-SUM]` и модальные конструкции,
  ловящиеся регулярками. `_deduplicate_explicit_codes` оставляет самый длинный текст на код.
- **PROD** **Смешанная.** Схема compile→validate→repair и запрет на генерацию идентификаторов
  моделью — переносимы. Регулярочное обнаружение — почти не нужно: ковенанты лежат ровно в
  «Статье 6» действующего договора под номерами `6.1`–`6.3`.
- **CONF** Высокая

### C-09 `sql` — сборка запроса

- **RESP** `CovenantSpec` → `WHERE`-выражение со связанными параметрами; границы окон.
- **CONTRACT** полуоткрытые интервалы `[start, end)`; все значения — параметры; LIKE экранируется
- **MOCK** нет
- **PROD** **Высокая.** Единственный компонент, переносимый почти без изменений.
- **CONF** Высокая

### C-10 `evaluators` — вычисление

- **RESP** Sum/Count/Max/Min/Avg/Ratio/Frequency; `TemporalEvaluationService` (версии);
  `compare()`; запись `Calculation`.
- **CONTRACT** `Decimal` вместо float; NULL→`0.000000` для SUM; отказ дробить агрегаты через
  смену версии
- **MOCK** Метрики покрывают ровно синтетические кейсы.
- **PROD** **Высокая для арифметики, низкая для покрытия.** Реальные ковенанты требуют EBITDA,
  условных (springing) тестов, «выручка минус наибольшая накладная статья», долей от выручки.
  Темпоральные версии решают не ту задачу: реальная проблема — выбор редакции документа.
- **CONF** Высокая

### C-11 `evidence` — подбор и проверка доказательства

- **RESP** `EvidenceMode` → селектор; независимый перевывод ожидаемой транзакции.
- **CONTRACT** `FirstViolating | Trigger | MaxTransaction`
- **MOCK** Режимы придуманы под синтетические кейсы.
- **PROD** **Низкая по семантике, высокая по паттерну.** Кейс требует *определяющую* транзакцию
  («уберите — вердикт изменится») и **прямо запрещает** «самую крупную» и «ту, что вывела за
  порог» — то есть текущие селекторы дают неверный ответ по определению. Независимая
  перепроверка как приём — сохранить.
- **CONF** Высокая

### C-12 `review` — ревью спецификаций (Cloud1) и ответов (codex-2)

- **RESP** `SpecReviewGraph`: review → grade_context → expand_retrieval → recompile.
  `ReviewService`: ревью готовых ответов с клеткой `_validate_decision`.
- **CONTRACT** `SpecReviewDecision` без полей числа/вердикта/доказательства
- **MOCK** Проверено только на синтетике; на реальных данных не запускалось.
- **PROD** **Средняя.** Идея «проверять интерпретацию до вычисления» верна и здесь. Но проверять
  придётся другое: выбор редакции документа, классификацию категорий, применение
  переклассификаций. `ReviewService` (путь codex-2) — тупик без потребителя.
- **CONF** Средняя

### C-13 `verification` — манифест, двойной путь, уверенность

- **RESP** `ManifestBuilder` (оракул полноты), `ResultVerifier`, `DualPathVerifier`,
  `compute_confidence`.
- **MOCK** Манифест собирается из вопросов + обнаруженных ковенантов.
- **PROD** **Высокая, с заменой источника.** В реальной задаче манифест задан жёстко:
  `submission_template.json` = 36 ячеек, пропуск = 0 баллов. `DualPathVerifier` написан, но
  **в горячий путь не подключён**.
- **CONF** Высокая

### C-14 `submission` — сериализация ответа

- **RESP** Внутренние результаты → внешний формат по профилю.
- **CONTRACT** `submission_profile.json`
- **MOCK** Формат выдуман.
- **PROD** **Нулевая.** Реальный формат задан `submission_template.json` и не совпадает
  ни ключами, ни словарём значений.
- **CONF** Высокая

### C-15 `ask` + `reporting` — вопросы и HTML-отчёт

- **RESP** Маршрутизация свободного вопроса; рендер ответа в терминал и HTML.
- **MOCK** Построено по просьбе «потыкать», не по требованиям.
- **PROD** **Нулевая.** Вопросов в задаче нет — есть фиксированный шаблон.
- **CONF** Высокая (подтверждено пользователем)

### C-16 `synthetic` + `benchmark` + `evals` — mock-датасет и его прогон

- **RESP** Генерация PDF/XLSX/эталонов; покомпонентный подсчёт баллов.
- **DEPS** reportlab, шрифты DejaVu; `synthetic` **зависит от `pipeline`**
- **MOCK** Целиком.
- **PROD** **Нулевая как данные, ненулевая как каркас.** Реальный `ground_truth.json` даёт
  настоящий эталон; механизм подсчёта баллов пригоден после замены шкалы на кейсовую
  (0.50/0.30/0.20 с линейным убыванием).
- **CONF** Высокая

### C-17 `observability` — трассировка

- **RESP** `trace_stage` поверх `langsmith.traceable`, метаданные через contextvar, редакция полей.
- **MOCK** нет
- **PROD** **Высокая.** Переносится как есть.
- **CONF** Высокая

### C-18 `pipeline` — оркестрация

- **RESP** `PreprocessPipeline`, `BatchEvaluationPipeline`, `ReviewPipeline`.
- **DEPS** 10 пакетов
- **MOCK** Порядок «структурные файлы раньше PDF» — верное решение, но опирается на наличие
  справочника заёмщиков.
- **PROD** **Средняя.** Скелет пригоден, состав шагов меняется.
- **CONF** Высокая

---

## 5. MOCK / LEGACY INVENTORY

### 5.1 Существует только ради mock-кейса

| Артефакт | Объём | Основание |
| --- | --- | --- |
| `synthetic/` | 2 050 строк, 12 файлов | генератор синтетического датасета |
| `benchmark/` | 439 строк | прогон по синтетическому эталону |
| `evals/` | 192 строки | покомпонентный подсчёт по синтетике |
| `scripts/cloud1_demo.py` | 257 строк | демо на синтетике |
| `scripts/regression_v2.py` | — | регрессия по синтетике |
| `data/synthetic/` | 8 JSON + 2 PDF + XLSX | mock-данные |
| CLI `generate-synthetic`, `benchmark`, `benchmark-full` | ~90 строк | — |
| compose-сервисы `generate-synthetic`, `benchmark` | — | **сервисы по умолчанию** |
| reportlab + шрифты DejaVu | зависимость | нужны только генератору |

### 5.2 Построено под неверные требования

| Артефакт | Объём | Основание |
| --- | --- | --- |
| `ask/` | 424 строки | вопросов в задаче нет |
| `reporting/` | 404 строки | HTML-отчёт не требуется |
| `ask.bat`, `ask.sh`, `../ask.bat` | — | лаунчеры |
| CLI `ask`, `answer-report` | ~110 строк | — |
| `submission/` | 175 строк | формат ответа выдуман |
| CLI `serialize-submission`, `validate-submission` | ~40 строк | — |

### 5.3 Тупиковые ветви

| Артефакт | Основание |
| --- | --- |
| `review/service.py`, `models.py`, `similarity.py`, `rationale.py`, `langchain_reviewer.py`, `storage.py`, `reviewer.py` | путь codex-2: ревью готовых ответов, потребителя нет (доказано в [06](06_CODEX_1_VS_CODEX_2.md)) |
| `pipeline/review.py`, `review_cli.py` | точка входа `halyk-review` для того же тупика |
| `verification/dual_path.py` | написан, покрыт тестами, **не вызывается из горячего пути** |
| `covenants/detector.py` — казахские паттерны | добавлены мной по неверному предположению |

### 5.4 Жёстко зашитые значения и допущения

| Что | Где | Проблема |
| --- | --- | --- |
| KZT как валюта примеров и порогов | `benchmark/reporting.py`, `benchmark/runner.py` | реальные данные в USD/EUR |
| `{"KZT","USD","EUR","RUB"}` | `covenants/validation.py:62` | закрытый список валют |
| `direction ∈ {incoming, outgoing}` | `storage/duckdb_store.py:31` | колонки в реальных данных нет |
| Пути к шрифтам DejaVu только под Linux | `synthetic/fonts.py` | опечатка `fontss` исправлена мной |
| `native_text_min_chars = 80` | `config.py` | не проверено на реальном корпусе |
| Порог нечёткого совпадения 85 | `borrowers/resolver.py` | не проверен на реальных названиях |
| Ветки `codex-1`/`codex-2` в CI | `.github/workflows/codex-1-ci.yml` | текущая ветка не собирается |

### 5.5 Сокращения вокруг валидации

| Что | Где |
| --- | --- |
| Отсутствие lock-файла | корень проекта |
| `assert` вместо проверки в сборке окна | `sql/builder.py:73` |
| Тавтологичная проверка полноты (исправлена мной, но источник манифеста ещё синтетический) | `pipeline/evaluate.py` |

---

## 6. AREAS OF UNCERTAINTY

Требуют разрешения на реальных данных до всякого проектирования.

| № | Неизвестное | Почему блокирует |
| --- | --- | --- |
| U-01 | В каком виде аудиторский отчёт задаёт переклассификации — таблицей, прозой, по `txn_id`? | Определяет, нужен ли LLM-разбор или хватит структурного |
| U-02 | Как KYC перечисляет связанные стороны и как они сопоставляются с полем `counterparty`? | Определяет механизм связывания: точное совпадение, нечёткое, через LLM |
| U-03 | Сколько категорий требуется и различимы ли они по `description` детерминированно? | **Центральный вопрос архитектуры.** Если нет — нужен слой классификации на LLM с проверкой |
| U-04 | Определён ли EBITDA внутри договора или требует внешнего знания? | Определяет, вычислим ли ковенант вообще |
| U-05 | Как отличить действующую редакцию от недействующей в общем случае? | Строка-маркер найдена на 2 примерах из 12; нужна проверка на всех |
| U-06 | 15 строк EUR: конвертировать (курс откуда?), исключать, или они вне сценарных расчётов? | Влияет на `actual` при точности 5 % |
| U-07 | Две пустые суммы (`TXN-P7-0033`, `TXN-P8-0031`): исключать или восстанавливать из документов? | То же |
| U-08 | «Определяющая» транзакция выводится правилом или требует перебора (убрать → пересчитать)? | Определяет стоимость и архитектуру шага доказательства |
| U-09 | Что содержат `4a5315740e89.csv` и `904dea48b34b.txt` среди PDF? | Могут быть данными, а не шумом |
| U-10 | Какой из 200 PDF без текстового слоя и относится ли он к сценарию? | Решает судьбу всего OCR-стека |
| U-11 | Роль ~134 документов, не содержащих сценарных `ACC` | Шум или связь через название компании |
| U-12 | Назначение `scripts/evaluate.py` | Не прочитан |

---

## 7. Итог стадии

### Current architecture

Двухфазный конвейер на Python 3.12: **фаза интерпретации** (PDF → блоки → кандидаты → LLM-компиляция
в `CovenantSpec` → LLM-ревью спецификации) и **фаза вычисления** (`CovenantSpec` → SQL по DuckDB →
число → вердикт → доказательство → верификация). LLM изолирована от арифметики закрытым каталогом
полей и связанными параметрами. Оркестрация — LangGraph, наблюдаемость — LangSmith, упаковка —
Docker. 22 пакета, ~11 000 строк, 6 136 строк тестов, ноль маркеров техдолга.

### Current data flow

```
CSV/XLSX ──► DuckDB.transactions
PDF ──► DocumentBlock ──► CovenantCandidate ──► CovenantSpec ──► SQL ──► CovenantResult
                                    ▲                  │
                            LLM (компиляция,      детерминированно
                             починка, ревью)
```

### Mock assumptions

Тринадцать решений продиктованы синтетическим набором: колонка `direction`; справочник заёмщиков;
`covenant_id` как свободная строка; словарь `complied`/`violated`; знаковое `number`; режимы
доказательства; OCR как первоклассный путь; темпоральные версии ковенанта вместо выбора редакции
документа; регулярочное обнаружение; валюта KZT; выдуманный формат ответа; интерфейс вопросов;
казахский язык.

### Potential legacy

Около **4 000 строк** кандидатов на удаление: `synthetic` + `benchmark` + `evals` (2 681),
`ask` + `reporting` (828), `submission` (175), путь ревью ответов codex-2 (~640), плюс CLI-команды,
лаунчеры и compose-сервисы. Ни одна строка не удаляется до стадии CLEANUP и до проверки ссылок.

### Things that appear reusable

`sql` (сборка запросов, полуоткрытые интервалы, связанные параметры) — почти без изменений.
`observability` — как есть. Арифметическое ядро `evaluators` (`Decimal`, `compare`, провенанс
`Calculation`). Механизмы: изоляция сбоев `FailureStage`, идемпотентность по SHA-256, независимая
перепроверка доказательства, отказ при неоднозначности вместо угадывания, ревью интерпретации до
вычисления, манифест ожиданий, клетка `_validate_decision`. Каркас `pipeline` и схема
`compile → validate → repair`.

### Unknowns that must be resolved from real data

Двенадцать пунктов §6. Блокирующие для проектирования: **U-03** (различимы ли категории по
`description`), **U-01** и **U-02** (форма переклассификаций и связанных сторон), **U-08**
(как определяется определяющая транзакция). Без ответов на них любая целевая архитектура будет
такой же догадкой, какой была прежняя.

---

**Стадия 1 завершена. Ни один файл не изменён.**
Следующая стадия — **DATA MODEL**: разбор реальных аудиторских отчётов, KYC-досье и текстов
`description` с ответами на U-01…U-12.
