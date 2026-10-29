from pathlib import Path

import pytest

SAMPLE_PDF = Path(__file__).resolve().parent.parent / "samples" / "contract.pdf"


@pytest.fixture
def sample_pdf() -> bytes:
    return SAMPLE_PDF.read_bytes()
