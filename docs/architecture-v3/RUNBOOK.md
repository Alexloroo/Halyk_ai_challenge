# RUNBOOK — как запустить Cloud1

> Пошаговая инструкция. Каноническое окружение — **Docker**: в образе уже стоит Python 3.12 и
> шрифты DejaVu, без которых не работает генерация синтетики.
>
> Архитектура: [09_ARCHITECTURE_CLOUD1.md](09_ARCHITECTURE_CLOUD1.md) ·
> План до 9 августа: [PLAN_HACKATHON.md](PLAN_HACKATHON.md)

---

## Оглавление

1. [Быстрый старт](#1-быстрый-старт)
2. [Почему Docker, а не локальный Python](#2-почему-docker-а-не-локальный-python)
3. [Установка](#3-установка)
4. [Ключ DeepSeek](#4-ключ-deepseek)
5. [Куда класть данные](#5-куда-класть-данные)
6. [Форматы файлов](#6-форматы-файлов)
7. [Полный прогон](#7-полный-прогон)
8. [Разбор каждой команды](#8-разбор-каждой-команды)
9. [Флаги Cloud1](#9-флаги-cloud1)
10. [Что читать в результатах](#10-что-читать-в-результатах)
11. [Тесты](#11-тесты)
12. [Локальный запуск без Docker](#12-локальный-запуск-без-docker)
13. [Проблемы](#13-проблемы)

---

## 1. Быстрый старт

```bash
cd 2_ARCHITECTURE_COVENANT_MVP
```

```bash
docker compose --profile test run --rm test
```

Если тесты зелёные — окружение исправно, можно работать.

---

## 2. Почему Docker, а не локальный Python

| | Docker | Локальный Python |
| --- | --- | --- |
| Версия Python | 3.12 из образа | нужна ваша, ≥3.12 |
| Шрифты DejaVu | стоят через `fonts-dejavu-core` | ставить руками |
| Синтетические тесты | работают | падают без шрифтов |
| Совпадение с CI | полное | зависит от машины |

Проверено на практике: 16 тестов из 239 требуют шрифт DejaVu. Ни в Windows, ни в стандартном
Python его нет. В образе он есть.

---

## 3. Установка

Нужен только Docker Desktop. Запустите его — демон должен работать, иначе команды `docker`
завершатся ошибкой подключения.

Проверка:

```bash
docker info --format "{{.ServerVersion}}"
```

Первая сборка образа занимает 3–5 минут, дальше кешируется.

---

## 4. Ключ DeepSeek

Создайте файл `.env` в папке `2_ARCHITECTURE_COVENANT_MVP`:

```
DEEPSEEK_API_KEY=sk-ваш-ключ
```

`docker compose` подхватывает `.env` автоматически. Файл содержит секрет — в git не коммитить.

Без ключа работает всё, кроме компиляции ковенантов и ревью спецификаций: тесты, генерация
синтетики, бенчмарк детерминированной части.

---

## 5. Куда класть данные

```
2_ARCHITECTURE_COVENANT_MVP/
└── data/
    ├── raw/                    ← ваши данные
    │   ├── documents/          ← PDF договоров
    │   └── transactions/       ← CSV/XLSX
    │       ├── borrowers.csv
    │       └── transactions.csv
    ├── questions.json          ← вопросы организатора
    ├── duckdb/                 ← база, создаётся сама
    └── submissions/            ← результаты
```

Подпапки можно называть как угодно — обход рекурсивный. Важно расширение:

| Расширение | Обработка |
| --- | --- |
| `.pdf` | OCR → поиск ковенантов → компиляция |
| `.csv` `.xlsx` `.xlsm` `.parquet` | загрузка транзакций |
| прочее | пропускается |

Структурные файлы обрабатываются раньше PDF — чтобы при компиляции заёмщики уже были в базе.

---

## 6. Форматы файлов

### `borrowers.csv`

```csv
borrower_id,canonical_name,bin
B001,Alpha Trade,990140000001
B002,Beta Logistics,990140000002
```

| Колонка | Обязательна | Назначение |
| --- | --- | --- |
| `borrower_id` | да | ключ, по которому всё связывается |
| `canonical_name` | да | название как в договоре |
| `bin` | нет | помогает найти заёмщика в тексте PDF |

### `transactions.csv`

```csv
transaction_id,borrower_id,transaction_date,amount,currency,direction,counterparty_name,purpose
TX-A1,B001,2026-04-01,5000000,KZT,outgoing,Vendor One LLP,Оплата по договору A-11
```

| Колонка | Обязательна | Формат |
| --- | --- | --- |
| `transaction_id` | **да** | уникальная строка |
| `transaction_date` | **да** | `ГГГГ-ММ-ДД`, можно назвать `date` |
| `amount` | **да** | число, точка как разделитель |
| `borrower_id` | нет | ссылка на `borrowers.csv` |
| `currency` | нет | `KZT`, `USD`, `EUR`, `RUB` |
| `direction` | нет | см. ниже |
| `counterparty_id`, `counterparty_name`, `purpose`, `account_id` | нет | — |

Синонимы `direction` приводятся к норме автоматически:

| Пишете | Становится |
| --- | --- |
| `in`, `credit`, `входящий`, `входящая` | `incoming` |
| `out`, `debit`, `исходящий`, `исходящая` | `outgoing` |

### `questions.json`

```json
[
  {
    "borrower_id": "B001",
    "covenant_id": "COV-ALPHA-SUM",
    "question": "Соблюдён ли месячный лимит исходящих KZT-платежей за апрель?"
  }
]
```

Квадратные скобки снаружи, между блоками запятая, после последнего — нет.

Не знаете `covenant_id`? Сначала обработайте документы, потом посмотрите список:

```bash
docker compose run --rm --entrypoint halyk-covenants generate-synthetic inspect-covenants --db /app/data/duckdb/run.duckdb
```

---

## 7. Полный прогон

```bash
docker compose --profile ai run --rm preprocess-ai
```

```bash
docker compose --profile ai run --rm evaluate-all
```

Своя команда с нужными путями:

```bash
docker compose run --rm --entrypoint halyk-covenants generate-synthetic preprocess /app/data/raw --db /app/data/duckdb/run.duckdb
```

```bash
docker compose run --rm --entrypoint halyk-covenants generate-synthetic evaluate-all --at-date 2026-08-05 --db /app/data/duckdb/run.duckdb --questions /app/data/questions.json --output /app/data/submissions/results.json --confidence-output /app/data/submissions/confidence-report.json
```

```bash
docker compose run --rm --entrypoint halyk-covenants generate-synthetic serialize-submission --results /app/data/submissions/results.json --profile /app/config/submission_profile.json --output /app/data/submissions/submission.json
```

> Пути внутри контейнера начинаются с `/app/`. Папка `./data` примонтирована в `/app/data`,
> поэтому результаты появляются у вас на диске.

---

## 8. Разбор каждой команды

### `preprocess` — пайплайн 1

```
1. CSV/XLSX      → таблица transactions
2. PDF → OCR     → таблица document_blocks
3. детектор ищет ковенанты (EN / RU / KZ)
4. загружается эмбеддер, строится гибридный поиск
5. LLM компилирует текст пункта → CovenantSpec       ← вызов LLM #1
6. ★ РЕВЬЮ СПЕЦИФИКАЦИИ                               ← вызов LLM #2
     отклонено? → перекомпиляция → повторное ревью    ← вызовы #3, #4
7. сохранение в covenants с меткой spec_trust
```

**Единственная команда, обращающаяся к LLM.** Кешируется по SHA-256 — повторный запуск на тех же
файлах пропускает работу.

Подробный лог, включая работу ревьюера:

```bash
docker compose run --rm --entrypoint halyk-covenants generate-synthetic --log-level INFO preprocess /app/data/raw --db /app/data/duckdb/run.duckdb
```

### `inspect-covenants`

Печатает скомпилированные спецификации. Здесь виден результат Cloud1 — поле `spec_trust`:

| Значение | Смысл |
| --- | --- |
| `accepted` | ревьюер принял с первого раза |
| `revised` | отклонил, перекомпиляция помогла |
| `low` | отклонил дважды — **посмотреть глазами** |

Плюс `review_objection` — что именно не понравилось.

### `evaluate-all` — пайплайн 2

Вызовов LLM: **ноль**. Только SQL и Python.

```
1. строится МАНИФЕСТ ОЖИДАНИЙ
2. по каждой паре: SQL → число → вердикт → доказательство
3. полнота проверяется ПРОТИВ МАНИФЕСТА, а не сама против себя
4. пишется confidence-report.json
```

### `serialize-submission` / `validate-submission`

Формат ответа организатора не менялся — Cloud1 совместим с существующей проверкой.

---

## 9. Флаги Cloud1

Всё включено по умолчанию, отключается флагом.

| Флаг | Команда | Что делает |
| --- | --- | --- |
| `--spec-review` / `--no-spec-review` | `preprocess` | Ревью спецификаций + перекомпиляция |
| `--manifest` / `--no-manifest` | `evaluate-all` | Полнота против манифеста |
| `--questions ФАЙЛ` | `evaluate-all` | Независимый источник ожиданий |
| `--confidence-output ФАЙЛ` | `evaluate-all` | Отчёт с рангом разбора |

### Сравнение с codex-1 без переключения веток

```bash
docker compose run --rm --entrypoint halyk-covenants generate-synthetic preprocess /app/data/raw --db /app/data/duckdb/base.duckdb --no-spec-review
```

```bash
docker compose run --rm --entrypoint halyk-covenants generate-synthetic evaluate-all --at-date 2026-08-05 --db /app/data/duckdb/base.duckdb --no-manifest --output /app/data/submissions/base-results.json
```

Разница между `base-results.json` и `results.json` — вклад Cloud1.

---

## 10. Что читать в результатах

### `confidence-report.json` — главный файл для человека

Отсортирован: самое сомнительное сверху.

```json
[
  {
    "borrower_id": "B003",
    "covenant_id": "COV-007",
    "level": "low",
    "triage_rank": 1,
    "flags": ["evidence_mismatch"],
    "spec_trust": "low",
    "review_objection": "Спецификация считает SUM, но текст требует количество операций"
  }
]
```

| Уровень | Когда | Что делать |
| --- | --- | --- |
| `unreliable` | расхождение двойного пути или `failed` | **разобрать обязательно** |
| `low` | `spec_trust=low` или несовпадение доказательства | разобрать |
| `medium` | спека переписана, или частичный результат | посмотреть по возможности |
| `high` | всё чисто, обе уверенности ≥ 0.70 | можно доверять |

Идите с `triage_rank: 1` вниз, пока есть время.

### `results.json`

Полный отчёт. Смотрите `verification.issues` — коды `missing_result` и `unexpected_result` теперь
**работают** (в codex-1 и codex-2 сработать не могли).

---

## 11. Тесты

```bash
docker compose --profile test run --rm test
```

Ожидаемо: **255 passed, 3 skipped**. Пропущенные — live-тесты, требующие ключей API.

Отдельно тесты Cloud1:

```bash
docker compose --profile test run --rm test python -m pytest tests/unit/test_spec_review.py tests/unit/test_cloud1_verification.py -v
```

Что они проверяют:

| Файл | Проверки |
| --- | --- |
| `test_spec_review.py` | тип решения ревьюера не содержит полей числа/вердикта/доказательства; попытка их подсунуть отвергается; отклонение доводит возражение до компилятора; перекомпиляция ровно одна; дважды отклонённая спека всё равно оценивается |
| `test_cloud1_verification.py` | манифест ловит вопрос, который детектор не нашёл; `missing_result` срабатывает; приоритет правил уверенности; ранжирование разбора; двойной путь ловит расхождение; групповые ковенанты не затирают расчёты друг друга |

---

## 12. Локальный запуск без Docker

Работает, но 16 синтетических тестов упадут без шрифтов DejaVu.

```bash
cd 2_ARCHITECTURE_COVENANT_MVP
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\Activate.ps1
```

Linux / macOS:

```bash
source .venv/bin/activate
```

```bash
pip install -e ".[dev,semantic]"
```

Прогон без шрифтозависимых тестов:

```bash
pytest --ignore=tests/integration/test_synthetic_generator.py --ignore=tests/integration/test_synthetic_renderers.py --ignore=tests/integration/test_synthetic_cli.py --ignore=tests/integration/test_synthetic_regression_v2.py --ignore=tests/integration/test_benchmark_runner.py --ignore=tests/integration/test_full_pipeline_e2e.py --ignore=tests/integration/test_regression_v2_runner.py
```

Если шрифты DejaVu у вас есть, укажите папку — тогда пройдёт всё:

```bash
export HALYK_DEJAVU_DIR=/путь/к/папке/со/шрифтами
```

---

## 13. Проблемы

### `failed to connect to the docker API`

Docker Desktop не запущен. Запустите приложение и дождитесь статуса «Running».

### `DejaVuSans.ttf and DejaVuSans-Bold.ttf are required`

Запуск вне контейнера без шрифтов. Варианты: перейти на Docker, поставить шрифты, или задать
`HALYK_DEJAVU_DIR`. Сообщение об ошибке перечисляет все просмотренные папки.

### `ModuleNotFoundError: No module named 'duckdb'`

Локальное окружение не активировано или проект не установлен: `pip install -e ".[dev]"`.

### При установке падает сборка `pyyaml`

Python 3.14 — колёс под него нет. Нужен 3.12, либо используйте Docker.

### `DeepSeekConfigurationError`

Не задан `DEEPSEEK_API_KEY`, см. [раздел 4](#4-ключ-deepseek).

### `Semantic embedder unavailable — falling back to BM25-only`

Не установлен extra `semantic`. Система работает, поиск контекста хуже. Это сообщение
**специально добавлено в Cloud1** — в codex-1 та же деградация происходила молча.

### `preprocess` сразу завершается, ничего не делая

Файлы уже обработаны, сработала идемпотентность по SHA-256. Удалите базу:

```bash
rm data/duckdb/run.duckdb
```

### Много ковенантов со `spec_trust: "low"`

Проверьте `review_objection`. Если возражения осмысленные — проблема в компиляции. Если ревьюер
придирается к верным спекам — временно отключите `--no-spec-review`.

---

## Шпаргалка

```bash
cd 2_ARCHITECTURE_COVENANT_MVP

# тесты
docker compose --profile test run --rm test

# полный прогон
docker compose --profile ai run --rm preprocess-ai
docker compose --profile ai run --rm evaluate-all

# что смотреть
# data/submissions/confidence-report.json  → сверху самое сомнительное
# data/submissions/submission.json         → файл ответа
```
