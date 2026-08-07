from __future__ import annotations

from pathlib import Path

import pymupdf

from halyk.docs import OCRConfig, load_documents


def _image_only_pdf(path: Path) -> None:
    with pymupdf.open() as source:
        page = source.new_page()
        page.insert_text((72, 72), "ACC-0001 scanned covenant")
        pixmap = page.get_pixmap(dpi=150, alpha=False)
    with pymupdf.open() as scanned:
        page = scanned.new_page(width=pixmap.width, height=pixmap.height)
        page.insert_image(page.rect, stream=pixmap.tobytes("png"))
        scanned.save(path)


def _native_pdf(path: Path) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "ACC-0002 native covenant text is already readable")
        document.save(path)


def test_image_only_page_uses_ocr_and_records_provenance(tmp_path: Path, monkeypatch) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    _image_only_pdf(documents_dir / "scan.pdf")
    monkeypatch.setenv("HALYK_PDF_WORKERS", "1")
    calls: list[tuple[str, int]] = []

    def fake_ocr(page, config: OCRConfig) -> str:
        calls.append((config.language, config.dpi))
        return "АСС-0001 Финансовые ковенанты Пункт 6.1"

    monkeypatch.setattr("halyk.docs._ocr_page", fake_ocr)

    documents = load_documents(
        documents_dir,
        ocr_config=OCRConfig(language="rus+kaz+eng", dpi=300, min_native_chars=20),
    )

    assert calls == [("rus+kaz+eng", 300)]
    assert documents[0].text == "ACC-0001 Финансовые ковенанты Пункт 6.1"
    assert documents[0].account_ids == ["ACC-0001"]
    assert documents[0].native_pages == []
    assert documents[0].ocr_pages == [1]
    assert documents[0].ocr_failed_pages == []
    assert documents[0].ocr_language == "rus+kaz+eng"
    assert documents[0].ocr_dpi == 300


def test_native_text_page_bypasses_ocr(tmp_path: Path, monkeypatch) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    _native_pdf(documents_dir / "native.pdf")
    monkeypatch.setenv("HALYK_PDF_WORKERS", "1")

    def fail_if_called(page, config: OCRConfig) -> str:
        raise AssertionError("OCR must not run for a readable native page")

    monkeypatch.setattr("halyk.docs._ocr_page", fail_if_called)

    document = load_documents(documents_dir, ocr_config=OCRConfig())[0]

    assert "ACC-0002" in document.text
    assert document.native_pages == [1]
    assert document.ocr_pages == []


def test_ocr_failure_keeps_document_and_records_page_issue(tmp_path: Path, monkeypatch) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    _image_only_pdf(documents_dir / "broken-ocr.pdf")
    monkeypatch.setenv("HALYK_PDF_WORKERS", "1")

    def failing_ocr(page, config: OCRConfig) -> str:
        raise RuntimeError("tessdata missing")

    monkeypatch.setattr("halyk.docs._ocr_page", failing_ocr)
    issues = []

    documents = load_documents(
        documents_dir,
        issues=issues,
        ocr_config=OCRConfig(),
    )

    assert len(documents) == 1
    assert documents[0].ocr_failed_pages == [1]
    assert issues[0].operation == "ocr"
    assert issues[0].page == 1
    assert issues[0].message == "tessdata missing"
