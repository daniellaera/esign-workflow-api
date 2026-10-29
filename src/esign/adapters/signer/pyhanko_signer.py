"""PAdES signing and validation with pyHanko.

Each signature is appended as an incremental update: the bytes covered by
earlier signatures are left untouched, so every signature stays verifiable.
"""

from datetime import timedelta
from io import BytesIO
from typing import Any

from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.misc import PdfError
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pyhanko.sign.validation import async_validate_pdf_signature
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.registry import SimpleCertificateStore

from esign.adapters.signer.test_ca import LocalTestCA
from esign.application.ports import Clock, SignatureCheck, SignerIdentity
from esign.domain.errors import InvalidDocumentError


def _to_asn1_certificate(certificate: x509.Certificate) -> Any:
    """pyHanko speaks asn1crypto; we build certificates with `cryptography`."""
    return asn1_x509.Certificate.load(certificate.public_bytes(serialization.Encoding.DER))


def _to_asn1_private_key(key: ec.EllipticCurvePrivateKey) -> Any:
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return asn1_keys.PrivateKeyInfo.load(der)


class PyHankoSignatureService:
    def __init__(self, ca: LocalTestCA, *, certificate_validity: timedelta, clock: Clock) -> None:
        self._ca = ca
        self._ca_asn1 = _to_asn1_certificate(ca.certificate)
        self._certificate_validity = certificate_validity
        self._clock = clock

    async def ensure_signable(self, pdf: bytes) -> None:
        try:
            reader = PdfFileReader(BytesIO(pdf))
            if reader.encrypted:
                raise InvalidDocumentError("encrypted PDFs are not supported")
            # Touching the page tree forces the parser through the document structure.
            reader.root["/Pages"]
        except (PdfError, KeyError, ValueError) as exc:
            raise InvalidDocumentError(f"PDF could not be parsed: {exc}") from exc

    async def sign(
        self, pdf: bytes, signer: SignerIdentity, *, field_name: str, reason: str
    ) -> bytes:
        issued = self._ca.issue(
            signer.name, signer.email, self._clock(), self._certificate_validity
        )
        cms_signer = signers.SimpleSigner(
            signing_cert=_to_asn1_certificate(issued.certificate),
            signing_key=_to_asn1_private_key(issued.private_key),
            # Embedding the CA certificate lets verifiers build the full chain.
            # (from_certs lacks annotations upstream, hence the ignore.)
            cert_registry=SimpleCertificateStore.from_certs([self._ca_asn1]),  # type: ignore[no-untyped-call]
        )
        metadata = signers.PdfSignatureMetadata(
            field_name=field_name,
            md_algorithm="sha256",
            subfilter=fields.SigSeedSubFilter.PADES,
            name=signer.name,
            reason=reason,
        )
        writer = IncrementalPdfFileWriter(BytesIO(pdf))
        output = await signers.PdfSigner(metadata, cms_signer).async_sign_pdf(writer)
        signed: bytes = output.getvalue()
        return signed

    async def verify(self, pdf: bytes) -> list[SignatureCheck]:
        reader = PdfFileReader(BytesIO(pdf))
        return [await self._check(embedded) for embedded in reader.embedded_signatures]

    async def _check(self, embedded: Any) -> SignatureCheck:
        # Signer certificates live for minutes, so we validate the chain at the
        # signing time the signer claims. Without a trusted timestamp (PAdES-B-T)
        # that time is self-asserted; see docs/adr/0004-test-ca-and-signatures.md.
        context = ValidationContext(
            trust_roots=[self._ca_asn1],
            allow_fetching=False,
            revocation_mode="soft-fail",
            moment=embedded.self_reported_timestamp,
        )
        status = await async_validate_pdf_signature(embedded, context)
        subject = status.signing_cert.subject.native
        return SignatureCheck(
            field_name=str(embedded.field_name),
            signer_name=subject.get("common_name"),
            signer_email=subject.get("email_address"),
            signing_time=status.signer_reported_dt,
            intact=bool(status.intact),
            valid=bool(status.valid),
            chain_valid=bool(status.trusted),
            modification_level=(
                status.modification_level.name if status.modification_level else "UNKNOWN"
            ),
        )

    def trust_anchor_pem(self) -> bytes:
        return self._ca.certificate_pem()
