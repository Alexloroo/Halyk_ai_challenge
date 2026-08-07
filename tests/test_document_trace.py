from __future__ import annotations

import json
import os
from pathlib import Path

import fitz

from halyk.docs import DocumentLoadIssue, OCRConfig, _pdf_worker_count, load_documents
from halyk.tracing import TraceWriter
from halyk.tracing.documents import trace_pymupdf


def test_document_loader_reports_failed_pdfs_without_losing_valid_text(tmp_path: Path) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    valid = documents_dir / "valid.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "ACC-0001 Financial covenants")
        pdf.save(valid)
    (documents_dir / "broken.pdf").write_bytes(b"not a pdf")
    issues: list[DocumentLoadIssue] = []

    documents = load_documents(documents_dir, issues=issues)

    assert [document.name for document in documents] == ["valid.pdf"]
    assert "ACC-0001" in documents[0].text
    assert len(issues) == 1
    assert issues[0].path.name == "broken.pdf"
    assert issues[0].error_type
    assert issues[0].message

    writer = TraceWriter.create(tmp_path / "trace")
    trace_pymupdf(writer, documents, issues)
    index = json.loads(
        (writer.root / "04_pymupdf/index.json").read_text(encoding="utf-8")
    )
    assert index[0]["native_pages"] == [1]
    assert index[0]["ocr_pages"] == []
    assert index[0]["ocr_failed_pages"] == []
    assert index[0]["ocr_language"] == ""
    assert index[0]["ocr_dpi"] is None
    assert index[1]["operation"] == "pdf_read"
    assert index[1]["page"] is None


def test_parallel_document_load_preserves_sorted_documents_issues_and_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    for name, account in (("z.pdf", "ACC-0002"), ("a.pdf", "ACC-0001")):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), f"{account} Financial covenants with native text")
            pdf.save(documents_dir / name)
    (documents_dir / "broken.pdf").write_bytes(b"not a pdf")
    config = OCRConfig(enabled=False)

    monkeypatch.setenv("HALYK_PDF_WORKERS", "1")
    sequential_issues: list[DocumentLoadIssue] = []
    sequential = load_documents(documents_dir, issues=sequential_issues, ocr_config=config)

    monkeypatch.setenv("HALYK_PDF_WORKERS", "2")
    parallel_issues: list[DocumentLoadIssue] = []
    parallel = load_documents(documents_dir, issues=parallel_issues, ocr_config=config)

    assert parallel == sequential
    assert [document.name for document in parallel] == ["a.pdf", "z.pdf"]
    assert parallel[0].native_pages == [1]
    assert parallel[0].ocr_pages == []
    assert [(issue.path.name, issue.operation) for issue in parallel_issues] == [
        ("broken.pdf", "pdf_read")
    ]
    assert parallel_issues == sequential_issues


def test_pdf_worker_count_falls_back_for_invalid_configuration(monkeypatch) -> None:
    expected_default = min(4, os.cpu_count() or 1)

    monkeypatch.setenv("HALYK_PDF_WORKERS", "0")
    assert _pdf_worker_count() == expected_default
    monkeypatch.setenv("HALYK_PDF_WORKERS", "not-a-number")
    assert _pdf_worker_count() == expected_default
