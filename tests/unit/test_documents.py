import pytest

from esign.domain.documents import check_pdf_upload
from esign.domain.errors import DocumentTooLargeError, InvalidDocumentError


def test_accepts_pdf_within_limit() -> None:
    check_pdf_upload(b"%PDF-1.7\n...", max_bytes=100)


def test_rejects_non_pdf() -> None:
    with pytest.raises(InvalidDocumentError):
        check_pdf_upload(b"PK\x03\x04 a zip file", max_bytes=100)


def test_rejects_oversized_file_before_looking_at_content() -> None:
    with pytest.raises(DocumentTooLargeError) as caught:
        check_pdf_upload(b"%PDF-" + b"x" * 100, max_bytes=100)
    assert caught.value.limit == 100
