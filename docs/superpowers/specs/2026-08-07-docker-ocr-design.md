# Docker OCR Fallback Design

## Goal

Make image-only PDF pages readable by the existing covenant pipeline, including `f3fa6d20c8a1.pdf`, without changing existing Makefile targets or flags.

## Design

`docs.py` continues to extract native text first. A page whose normalized native text is shorter than the configured minimum uses PyMuPDF's `get_textpage_ocr()` with Tesseract. OCR is post-page and selective, so text-native documents retain their current fast path. The recognized text enters the existing document classification, account mapping, rule extraction, and evaluation unchanged.

OCR defaults are `rus+kaz+eng`, 300 DPI, and a 20-character native-text threshold. Environment variables can tune these values without adding CLI or Makefile flags. OCR failures are diagnostic: the document remains present with its native text, and fulltrace records the failed page and error.

The Docker image installs Tesseract and Russian, Kazakh, and English language data. Compose mounts the project at `/app`, so the existing `make run` and `make fulltrace` commands and their current flags run unchanged inside the container.

## Trace Contract

The existing `04_pymupdf/index.json` and `05_documents_classified/documents.json` records gain `native_pages`, `ocr_pages`, `ocr_failed_pages`, `ocr_language`, and `ocr_dpi`. The per-document `.txt` contains the combined native/OCR text used downstream.

## Verification

Unit tests create an image-only PDF and verify selective OCR, native-page bypass, and graceful OCR failure. Docker verification checks Tesseract languages, extracts `f3fa6d20c8a1.pdf`, and proves the resulting text maps to `P6/6.1`. The complete suite and Ruff must remain green.
