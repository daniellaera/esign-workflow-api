"""Uploaded PDF documents."""

from dataclasses import dataclass
from datetime import datetime

from esign.domain.errors import DocumentTooLargeError, InvalidDocumentError

PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True)
class Document:
    id: str
    filename: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime


def check_pdf_upload(data: bytes, max_bytes: int) -> None:
    """Cheap checks done before any parsing: size limit and PDF magic bytes."""
    if len(data) > max_bytes:
        raise DocumentTooLargeError(len(data), max_bytes)
    if not data.startswith(PDF_MAGIC):
        raise InvalidDocumentError("file does not start with the %PDF- header")
