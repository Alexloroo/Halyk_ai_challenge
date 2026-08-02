from typing import Protocol

from halyk_covenants.domain import DocumentBlock


class VisualDocumentProvider(Protocol):
    def extract(self, image: bytes, *, document_id: str, page: int) -> list[DocumentBlock]: ...
