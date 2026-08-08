"""Trace PyMuPDF text extraction and document classification."""

from __future__ import annotations

from halyk.docs import Document, DocumentLoadIssue

from .writer import TraceWriter


def document_record(document: Document) -> dict[str, object]:
    return {
        "source": document.path,
        "name": document.name,
        "pages": document.pages,
        "characters": len(document.text),
        "kind": document.kind,
        "edition": document.edition,
        "account_ids": document.account_ids,
        "native_pages": document.native_pages,
        "ocr_pages": document.ocr_pages,
        "ocr_failed_pages": document.ocr_failed_pages,
        "ocr_language": document.ocr_language,
        "ocr_dpi": document.ocr_dpi,
    }


def trace_pymupdf(
    writer: TraceWriter,
    documents: list[Document],
    issues: list[DocumentLoadIssue],
) -> None:
    index: list[dict[str, object]] = []
    for document in documents:
        artifact = writer.write_text("04_pymupdf", f"{document.path.stem}.txt", document.text)
        record = document_record(document)
        record["text_artifact"] = artifact.relative_to(writer.root).as_posix()
        record["status"] = "read"
        index.append(record)
    for issue in issues:
        index.append(
            {
                "source": issue.path,
                "name": issue.path.name,
                "status": "warning" if issue.operation == "ocr" else "error",
                "error_type": issue.error_type,
                "message": issue.message,
                "operation": issue.operation,
                "page": issue.page,
            }
        )
    writer.write_json("04_pymupdf", "index.json", index)
    writer.update_stage(
        "04_pymupdf",
        status="completed" if not issues else "completed_with_errors",
        documents=len(documents),
        errors=len(issues),
        error_details=issues,
    )


def trace_classified(
    writer: TraceWriter,
    documents: list[Document],
    decisions: list[dict[str, object]] | None = None,
) -> None:
    writer.write_json(
        "05_documents_classified",
        "documents.json",
        [document_record(document) for document in documents],
    )
    if decisions is not None:
        writer.write_json("05_documents_classified", "decisions.json", decisions)
    requested = sum(bool(record.get("llm_requested")) for record in decisions or [])
    resolved = sum(
        bool(record.get("llm_requested"))
        and record.get("final_kind") != record.get("initial_kind")
        for record in decisions or []
    )
    writer.update_stage(
        "05_documents_classified",
        documents=len(documents),
        document_llm_requested=requested,
        document_llm_reclassified=resolved,
        status="completed",
    )
