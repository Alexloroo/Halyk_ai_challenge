# Scanned PDF fixtures

Image-only PDFs are generated during tests so large binary fixtures are not stored in the
repository. `test_scanned_pdf_ingestion.py` creates a raster-only page and injects a deterministic
OCR provider. The real Paddle GPU path is covered by the opt-in `RUN_GPU_OCR_LIVE=1` smoke gate.
