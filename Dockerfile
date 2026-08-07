FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    HALYK_OCR_ENABLED=1 \
    HALYK_OCR_LANGUAGE=rus+kaz+eng \
    HALYK_OCR_DPI=300 \
    HALYK_OCR_MIN_NATIVE_CHARS=20

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        make \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-kaz \
        tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --editable ".[dev]"

COPY Makefile ./

CMD ["make", "fulltrace-local"]
