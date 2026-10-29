"""Real PAdES signing and validation with the test CA (no database needed)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID

from esign.adapters.signer.pyhanko_signer import PyHankoSignatureService, stamp_box
from esign.adapters.signer.test_ca import CA_COMMON_NAME, LocalTestCA
from esign.application.ports import SignerIdentity
from esign.domain.errors import InvalidDocumentError

NOW = datetime.now(UTC)
A4_WIDTH, A4_HEIGHT = 595, 842


@pytest.fixture(scope="module")
def service() -> PyHankoSignatureService:
    ca = LocalTestCA.generate(NOW)
    return PyHankoSignatureService(ca, certificate_validity=timedelta(hours=1), clock=lambda: NOW)


async def sign_twice(service: PyHankoSignatureService, pdf: bytes) -> bytes:
    once = await service.sign(
        pdf,
        SignerIdentity("Alice Example", "alice@example.com"),
        field_name="S1",
        reason="r",
        position=0,
    )
    return await service.sign(
        once,
        SignerIdentity("Bob Example", "bob@example.com"),
        field_name="S2",
        reason="r",
        position=1,
    )


async def test_two_incremental_signatures_both_validate(
    service: PyHankoSignatureService, sample_pdf: bytes
) -> None:
    signed = await sign_twice(service, sample_pdf)
    assert signed.startswith(sample_pdf)  # incremental update: original bytes untouched

    checks = await service.verify(signed)
    assert [c.signer_name for c in checks] == ["Alice Example", "Bob Example"]
    assert [c.signer_email for c in checks] == ["alice@example.com", "bob@example.com"]
    assert all(c.intact and c.valid and c.chain_valid for c in checks)
    assert checks[0].modification_level == "FORM_FILLING"  # Bob's signature was added after
    assert checks[1].modification_level == "NONE"


async def test_tampering_is_detected(service: PyHankoSignatureService, sample_pdf: bytes) -> None:
    signed = bytearray(await sign_twice(service, sample_pdf))
    position = signed.index(b"SAMPLE AGREEMENT")
    signed[position] = ord("X")
    checks = await service.verify(bytes(signed))
    assert not any(c.intact for c in checks)


async def test_signature_from_another_ca_is_not_trusted(
    service: PyHankoSignatureService, sample_pdf: bytes
) -> None:
    stranger = PyHankoSignatureService(
        LocalTestCA.generate(NOW), certificate_validity=timedelta(hours=1), clock=lambda: NOW
    )
    signed = await stranger.sign(
        sample_pdf,
        SignerIdentity("Eve", "eve@example.com"),
        field_name="S1",
        reason="r",
        position=0,
    )
    (check,) = await service.verify(signed)
    assert check.intact
    assert not check.chain_valid


async def test_unsigned_pdf_has_no_signatures(
    service: PyHankoSignatureService, sample_pdf: bytes
) -> None:
    assert await service.verify(sample_pdf) == []


async def test_ensure_signable_accepts_sample(
    service: PyHankoSignatureService, sample_pdf: bytes
) -> None:
    await service.ensure_signable(sample_pdf)


async def test_ensure_signable_rejects_garbage(service: PyHankoSignatureService) -> None:
    with pytest.raises(InvalidDocumentError):
        await service.ensure_signable(b"%PDF-1.7\nthis is not really a pdf")


def test_issued_certificate_names_the_signer_and_is_short_lived() -> None:
    ca = LocalTestCA.generate(NOW)
    issued = ca.issue("Alice Example", "alice@example.com", NOW, timedelta(minutes=30))
    cert = issued.certificate
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Alice Example"
    assert cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == CA_COMMON_NAME
    # X.509 stores whole seconds.
    assert NOW + timedelta(minutes=29) < cert.not_valid_after_utc <= NOW + timedelta(minutes=30)
    key_usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert key_usage.content_commitment


def test_ca_is_persisted_and_reloaded(tmp_path: Path) -> None:
    first = LocalTestCA.load_or_create(tmp_path, NOW)
    second = LocalTestCA.load_or_create(tmp_path, NOW)
    assert first.certificate == second.certificate
    assert (tmp_path / "test-ca-key.pem").stat().st_mode & 0o777 == 0o600


def boxes_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def test_stamp_boxes_never_overlap_and_stay_on_an_a4_page() -> None:
    boxes = [stamp_box(position) for position in range(10)]  # the API allows 10 signers
    for i, a in enumerate(boxes):
        assert min(a) >= 0
        assert a[2] <= A4_WIDTH
        assert a[3] <= A4_HEIGHT
        for b in boxes[i + 1 :]:
            assert not boxes_overlap(a, b)


def test_first_signer_is_on_top() -> None:
    assert stamp_box(0)[1] > stamp_box(1)[1]
