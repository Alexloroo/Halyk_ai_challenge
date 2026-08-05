# RUNBOOK — как запустить Cloud1

> Пошаговая инструкция для запуска системы оценки ковенантов с архитектурой Cloud1.
> Проект архитектуры: [09_ARCHITECTURE_CLOUD1.md](09_ARCHITECTURE_CLOUD1.md).

---

## Оглавление

1. [Требования](#1-требования)
2. [Установка](#2-установка)
3. [Настройка ключа DeepSeek](#3-настройка-ключа-deepseek)
4. [Полный прогон за пять команд](#4-полный-прогон-за-пять-команд)
5. [Разбор каждой команды](#5-разбор-каждой-команды)
6. [Флаги Cloud1](#6-флаги-cloud1)
7. [Что читать в результатах](#7-что-читать-в-результатах)
8. [Проверка без ключа LLM](#8-проверка-без-ключа-llm)
9. [Типичные проблемы](#9-типичные-проблемы)

---

## 1. Требования

| | |
| --- | --- |
| **Python** | **3.12** или 3.13. **НЕ 3.14** — зависимость `pyyaml==6.0.2` не собирается |
| Диск | ~2 ГБ (модель эмбеддингов ~120 МБ + зависимости) |
| Ключ API | DeepSeek — нужен для компиляции и ревью спецификаций |
| ОС | Windows / Linux / macOS |

Проверить версию Python:

```bash
python --version
```

Если стоит 3.14 — поставьте 3.12 рядом. На Windows удобнее через
[python.org](https://www.python.org/downloads/release/python-3128/), при установке отметить
«Add python.exe to PATH».

---

## 2. Установка

Все команды выполняются **из папки `2_ARCHITECTURE_COVENANT_MVP`**, а не из корня репозитория.

### 2.1 Перейти в папку проекта

```bash
cd 2_ARCHITECTURE_COVENANT_MVP
```

### 2.2 Создать виртуальное окружение

*Виртуальное окружение* — отдельная папка с библиотеками только для этого проекта, чтобы не
засорять систему.

```bash
python -m venv .venv
```

### 2.3 Активировать окружение

Windows (PowerShell):

```bash
.venv\Scripts\Activate.ps1
```

Linux / macOS:

```bash
source .venv/bin/activate
```

После активации в начале строки терминала появится `(.venv)`.

### 2.4 Установить проект

```bash
pip install -e ".[dev,semantic]"
```

Что означают части:

| Часть | Что даёт |
| --- | --- |
| `-e` | режим разработки — правки в коде подхватываются без переустановки |
| `dev` | pytest и ruff для тестов и проверки стиля |
| `semantic` | sentence-transformers для семантического поиска (Cloud1 его использует) |

Без `semantic` система запустится, но поиск контекста для компилятора деградирует до поиска по
ключевым словам. **Cloud1 напишет об этом в лог** — молчаливой деградации, как в `codex-1`, больше нет.

---

## 3. Настройка ключа DeepSeek

Создайте файл `.env` в папке `2_ARCHITECTURE_COVENANT_MVP`:

```
DEEPSEEK_API_KEY=sk-ваш-ключ-сюда
```

Либо задайте переменную окружения.

Windows (PowerShell):

```bash
$env:DEEPSEEK_API_KEY = "sk-ваш-ключ-сюда"
```

Linux / macOS:

```bash
export DEEPSEEK_API_KEY="sk-ваш-ключ-сюда"
```

> `.env` содержит секрет. Не коммитьте его в git.

---

## 4. Полный прогон за пять команд

```bash
halyk-covenants preprocess data/raw --db data/duckdb/run.duckdb
```

```bash
halyk-covenants inspect-covenants --db data/duckdb/run.duckdb
```

```bash
halyk-covenants evaluate-all --at-date 2026-08-05 --db data/duckdb/run.duckdb --output out/results.json --confidence-output out/confidence-report.json
```

```bash
halyk-covenants serialize-submission --results out/results.json --profile config/submission_profile.json --output out/submission.json
```

```bash
halyk-covenants validate-submission --submission out/submission.json --profile config/submission_profile.json
```

---

## 5. Разбор каждой команды

### Шаг 1 — `preprocess`

```bash
halyk-covenants preprocess data/raw --db data/duckdb/run.duckdb
```

Что происходит внутри (это **пайплайн 1** из архитектуры):

```
1. читает CSV/XLSX  →  таблица transactions
2. читает PDF       →  OCR  →  таблица document_blocks
3. детектор ищет ковенанты (EN / RU / KZ)
4. загружает эмбеддер, строит гибридный поиск
5. LLM компилирует текст пункта → CovenantSpec        ← вызов LLM #1
6. ★ РЕВЬЮ СПЕЦИФИКАЦИИ                                ← вызов LLM #2
      отклонено? → перекомпиляция → повторное ревью    ← вызовы #3, #4
7. сохраняет в таблицу covenants с меткой spec_trust
```

**Это единственная команда, которая обращается к LLM.** Результат кешируется по SHA-256 —
повторный запуск на тех же файлах пропустит всю работу.

Полезные флаги:

```bash
# посмотреть подробный лог, включая работу ревьюера
halyk-covenants --log-level INFO preprocess data/raw --db data/duckdb/run.duckdb

# включить OCR (нужен extra `ocr`, требует GPU)
halyk-covenants preprocess data/raw --db data/duckdb/run.duckdb --ocr

# выключить ревью спецификаций — получится поведение codex-1
halyk-covenants preprocess data/raw --db data/duckdb/run.duckdb --no-spec-review
```

### Шаг 2 — `inspect-covenants`

```bash
halyk-covenants inspect-covenants --db data/duckdb/run.duckdb
```

Печатает все скомпилированные спецификации. **Здесь видно результат работы Cloud1** — поле
`spec_trust` у каждого ковенанта:

| Значение | Что означает |
| --- | --- |
| `accepted` | ревьюер принял спецификацию с первого раза |
| `revised` | ревьюер отклонил, перекомпиляция помогла |
| `low` | отклонена дважды — **посмотрите на этот ковенант глазами** |

Плюс поля `review_objection` (что именно не понравилось ревьюеру) и `review_confidence`.

### Шаг 3 — `evaluate-all`

```bash
halyk-covenants evaluate-all \
  --at-date 2026-08-05 \
  --db data/duckdb/run.duckdb \
  --output out/results.json \
  --confidence-output out/confidence-report.json
```

Это **пайплайн 2**. Вызовов LLM здесь **ноль** — только SQL и Python.

```
1. строит МАНИФЕСТ ОЖИДАНИЙ (что вообще должно быть посчитано)
2. по каждой паре (заёмщик, ковенант):
      SQL → число → вердикт → доказательство
3. проверяет полноту ПРОТИВ МАНИФЕСТА, а не сам против себя
4. пишет confidence-report.json со списком на разбор
```

С файлом вопросов организатора (сильнее проверка полноты — источник не зависит от нашего
обнаружения):

```bash
halyk-covenants evaluate-all --at-date 2026-08-05 --db data/duckdb/run.duckdb --questions data/questions.json --output out/results.json --confidence-output out/confidence-report.json
```

Формат `questions.json`:

```json
[
  {"borrower_id": "B001", "covenant_id": "COV-001", "question": "Соблюдает ли заёмщик..."},
  {"borrower_id": "B002", "covenant_id": "COV-003", "question": "..."}
]
```

### Шаг 4 — `serialize-submission`

```bash
halyk-covenants serialize-submission --results out/results.json --profile config/submission_profile.json --output out/submission.json
```

Превращает внутренние результаты в формат ответа организатора. **Формат не менялся** — Cloud1
совместим с существующей проверкой.

### Шаг 5 — `validate-submission`

```bash
halyk-covenants validate-submission --submission out/submission.json --profile config/submission_profile.json
```

Проверяет файл ответа независимо от того, как он был получен. Код возврата `3` означает, что файл
не прошёл проверку.

---

## 6. Флаги Cloud1

Все новые возможности включены по умолчанию и отключаются флагом.

| Флаг | Команда | По умолчанию | Что делает |
| --- | --- | --- | --- |
| `--spec-review` / `--no-spec-review` | `preprocess` | включено | Ревью спецификаций + ограниченная перекомпиляция |
| `--manifest` / `--no-manifest` | `evaluate-all` | включено | Проверка полноты против манифеста вместо тавтологичной |
| `--questions ФАЙЛ` | `evaluate-all` | нет | Добавляет независимый источник ожиданий |
| `--confidence-output ФАЙЛ` | `evaluate-all` | нет | Пишет отчёт с рангом разбора |

### Как получить поведение codex-1 для сравнения

```bash
halyk-covenants preprocess data/raw --db data/duckdb/base.duckdb --no-spec-review
```

```bash
halyk-covenants evaluate-all --at-date 2026-08-05 --db data/duckdb/base.duckdb --no-manifest --output out/base-results.json
```

Потом сравните `out/base-results.json` с `out/results.json` — разница и есть вклад Cloud1.

---

## 7. Что читать в результатах

### `out/confidence-report.json` — главный файл для человека

Отсортирован так, что **самое сомнительное сверху**:

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

Уровни уверенности:

| Уровень | Когда | Что делать |
| --- | --- | --- |
| `unreliable` | расхождение двойного пути или статус `failed` | **разобрать обязательно** |
| `low` | `spec_trust=low` или несовпадение доказательства | разобрать |
| `medium` | спецификация переписана, или частичный результат | посмотреть по возможности |
| `high` | всё чисто, обе уверенности ≥ 0.70 | можно доверять |

Начинайте с `triage_rank: 1` и идите вниз, пока есть время.

### `out/results.json`

Полный отчёт: результаты + блок `verification` с найденными проблемами. Смотрите поле
`verification.issues` — коды `missing_result` и `unexpected_result` теперь **работают** (в codex-1 и
codex-2 они не могли сработать никогда).

### `out/submission.json`

Файл ответа для организатора. Формат тот же, что в codex-1/codex-2.

---

## 8. Проверка без ключа LLM

Если ключа DeepSeek нет, детерминированную часть можно проверить на синтетических данных:

```bash
halyk-covenants benchmark-full --output data/synthetic
```

Генерирует тестовый набор и прогоняет по нему вычислители. LLM не задействована.

Запустить тесты:

```bash
pytest
```

Проверить стиль кода:

```bash
ruff check src tests
```

---

## 9. Типичные проблемы

### `ModuleNotFoundError: No module named 'duckdb'`

Окружение не активировано или проект не установлен.

```bash
pip install -e ".[dev,semantic]"
```

### При установке падает сборка `pyyaml`

У вас Python 3.14. Нужен 3.12.

```bash
py -3.12 -m venv .venv
```

### В логе `Semantic embedder unavailable — falling back to BM25-only retrieval`

Не установлен extra `semantic`. Система работает, но поиск контекста хуже.

```bash
pip install -e ".[semantic]"
```

Это сообщение — **специально добавленное в Cloud1**. В `codex-1` та же деградация происходила
молча.

### `DeepSeekConfigurationError`

Не задан `DEEPSEEK_API_KEY`. См. [раздел 3](#3-настройка-ключа-deepseek).

### `preprocess` ничего не делает и сразу завершается

Файлы уже обработаны — сработала идемпотентность по SHA-256. Чтобы прогнать заново, удалите базу:

```bash
rm data/duckdb/run.duckdb
```

### Много ковенантов со `spec_trust: "low"`

Ревьюер массово отклоняет спецификации. Проверьте `review_objection` — если возражения осмысленные,
проблема в компиляции. Если ревьюер придирается к верным спецификациям, временно отключите:

```bash
halyk-covenants preprocess data/raw --db data/duckdb/run.duckdb --no-spec-review
```

---

## Шпаргалка

```bash
# установка
cd 2_ARCHITECTURE_COVENANT_MVP
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,semantic]"

# полный прогон
halyk-covenants preprocess data/raw --db data/duckdb/run.duckdb
halyk-covenants evaluate-all --at-date 2026-08-05 --db data/duckdb/run.duckdb --output out/results.json --confidence-output out/confidence-report.json
halyk-covenants serialize-submission --results out/results.json --profile config/submission_profile.json --output out/submission.json

# что смотреть
# out/confidence-report.json  → отсортирован, сверху самое сомнительное
# out/submission.json         → файл ответа
```
