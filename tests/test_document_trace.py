from __future__ import annotations

import json
from pathlib import Path

import fitz

from halyk.docs import DocumentLoadIssue, load_documents
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
