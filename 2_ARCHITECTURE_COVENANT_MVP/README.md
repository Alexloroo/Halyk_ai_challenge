# Halyk Covenant Evaluation MVP

This repository implements Phases 1–3 of `2_ARCHITECTURE_COVENANT_MVP.md`:

- strict Pydantic domain models;
- CSV, Excel, and Parquet transaction ingestion into DuckDB;
- deterministic SUM, COUNT, MAX, MIN, and AVG covenant evaluation;
- exact comparator semantics and MAX evidence selection;
- partial and failed results that do not discard valid answer components;
- a minimal end-to-end CLI.

PDF parsing, OCR, covenant discovery, LLM compilation, vector retrieval, and the official
submission serializer are deliberately outside this milestone.

## Setup

Python 3.12 or newer is required.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Canonical transaction input

CSV, `.xlsx`, and Parquet are supported. Required source columns are:

| Canonical field | Required | DuckDB type |
|---|---:|---|
| `transaction_id` | yes | `VARCHAR` |
| `date` or `transaction_date` | yes | `DATE` |
| `amount` | yes | `DECIMAL(38, 6)` |
| `borrower_id` | no | `VARCHAR` |
| `account_id` | no | `VARCHAR` |
| `currency` | no | `VARCHAR` |
| `direction` | no | `VARCHAR` |
| `counterparty_id` | no | `VARCHAR` |
| `counterparty_name` | no | `VARCHAR` |
| `purpose` | no | `VARCHAR` |
| `source_row_id` | no | `VARCHAR` |

Noncanonical headers can be adapted through `DuckDBStore.load_transactions(...,
column_mapping={canonical: source})`. Every row is also stored in `raw_transactions` as a JSON
payload with source path, row number, and SHA-256 hash. Exact duplicates are detectable but are
not silently removed.

## CLI

Evaluate a strict `CovenantSpec` JSON file against a transaction file:

```bash
.venv/bin/halyk-covenants evaluate \
  --transactions tests/fixtures/transactions.csv \
  --covenant covenant.json \
  --borrower-id 000341 \
  --at-date 2026-04-30
```

The result is the internal `CovenantResult` JSON. Decimal values are serialized as JSON strings
to avoid precision loss. Presentation rules for the future official submission belong in a
separate serializer and are not embedded in the evaluator.

## Tests and quality checks

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```
