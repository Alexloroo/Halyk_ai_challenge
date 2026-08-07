# Docker OCR Fallback Implementation Plan

> **For agentic workers:** Execute inline with test-first steps. Git operations are forbidden for this task.

**Goal:** Add selective OCR for image-only PDF pages using Docker-provided Tesseract.

**Architecture:** PyMuPDF native extraction remains primary. A filesystem-independent OCR configuration controls a per-page fallback; Docker supplies Tesseract language data, while trace serializers expose extraction provenance.

**Tech Stack:** Python 3.12, PyMuPDF, Tesseract 5, Docker Compose, pytest.

## Global Constraints

- Do not change existing Makefile targets, commands, or flags.
- Do not perform git operations.
- OCR only pages with fewer than 20 native text characters by default.
- OCR languages default to `rus+kaz+eng` at 300 DPI.
- OCR errors do not drop an otherwise readable document.

### Task 1: Selective OCR loader

**Files:** `tests/test_document_ocr.py`, `src/halyk/docs.py`

- [ ] Create a real image-only PDF fixture and a failing test for OCR fallback metadata.
- [ ] Run the test and confirm failure because OCR metadata/configuration is absent.
- [ ] Implement `OCRConfig`, per-page native/OCR extraction, and nonfatal page diagnostics.
- [ ] Verify image-only, native-text, and OCR-error cases.

### Task 2: Fulltrace OCR provenance

**Files:** `tests/test_document_trace.py`, `src/halyk/tracing/documents.py`

- [ ] Add failing assertions for native/OCR page lists, language, and DPI.
- [ ] Extend document records and PDF issue records with page-level OCR fields.
- [ ] Run tracing tests and the full unit suite.

### Task 3: Docker runtime

**Files:** `Dockerfile`, `compose.yaml`, `.dockerignore`, `README.md`

- [ ] Define Python 3.12 image with Tesseract `rus`, `kaz`, and `eng` data.
- [ ] Mount the repository at `/app` without modifying Makefile.
- [ ] Document `docker compose run --rm halyk make fulltrace ARGS=--no-llm`.
- [ ] Build the image and verify installed OCR languages.

### Task 4: Target regression and final verification

**Files:** no additional production files unless verification exposes a defect.

- [ ] OCR `data/raw/documents/f3fa6d20c8a1.pdf` inside Docker.
- [ ] Verify extracted account, document kind, clauses, and `P6/6.1` rule.
- [ ] Run all tests, Ruff, and Docker fulltrace.
- [ ] Inspect `04_pymupdf`, `05_documents_classified`, `10_rules/P6.json`, and ground-truth comparison.
