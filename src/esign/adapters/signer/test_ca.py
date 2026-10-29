"""A local certificate authority for demonstration only.

It mimics how remote-signing services work: for each signature, a fresh key
pair is generated and a short-lived certificate naming the signer is issued.
Nothing here is qualified, audited or protected by an HSM: the CA key is a
file on disk. The name says so on purpose.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

CA_COMMON_NAME = "TEST CA – NOT QUALIFIED"
CA_ORGANIZATION = "esign-workflow-api demo"
CA_VALIDITY = timedelta(days=3650)
# Tolerate small clock differences between this service and a verifier.
CLOCK_SKEW = timedelta(minutes=5)

_KEY_FILE = "test-ca-key.pem"
_CERT_FILE = "test-ca-cert.pem"


@dataclass(frozen=True)
class IssuedCertificate:
    private_key: ec.EllipticCurvePrivateKey
    certificate: x509.Certificate


def _key_usage(*, signing: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=signing,
        content_commitment=signing,  # a.k.a. non-repudiation, which pyHanko requires
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=not signing,
        crl_sign=not signing,
        encipher_only=False,
        decipher_only=False,
    )


class LocalTestCA:
    def __init__(self, private_key: ec.EllipticCurvePrivateKey, certificate: x509.Certificate):
        self._private_key = private_key
        self.certificate = certificate

    @classmethod
    def generate(cls, now: datetime) -> "LocalTestCA":
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, CA_ORGANIZATION),
            ]
        )
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - CLOCK_SKEW)
            .not_valid_after(now + CA_VALIDITY)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(_key_usage(signing=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
            .sign(key, hashes.SHA256())
        )
        return cls(key, certificate)

    @classmethod
    def load_or_create(cls, directory: Path, now: datetime) -> "LocalTestCA":
        """Reuse the CA from ``directory`` so signatures stay verifiable across restarts."""
        key_path, cert_path = directory / _KEY_FILE, directory / _CERT_FILE
        if key_path.exists() and cert_path.exists():
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey):
                raise TypeError(f"{key_path} does not hold an EC private key")
            return cls(key, x509.load_pem_x509_certificate(cert_path.read_bytes()))

        ca = cls.generate(now)
        directory.mkdir(parents=True, exist_ok=True)
        key_pem = ca._private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        # Owner read/write only.
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as key_file:
            key_file.write(key_pem)
        cert_path.write_bytes(ca.certificate_pem())
        return ca

    def certificate_pem(self) -> bytes:
        return self.certificate.public_bytes(serialization.Encoding.PEM)

    def issue(self, name: str, email: str, now: datetime, validity: timedelta) -> IssuedCertificate:
        """Issue a short-lived signing certificate for one signer."""
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, name),
                x509.NameAttribute(NameOID.EMAIL_ADDRESS, email),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, CA_ORGANIZATION),
            ]
        )
        ca_public_key = self._private_key.public_key()
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self.certificate.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - CLOCK_SKEW)
            .not_valid_after(now + validity)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(_key_usage(signing=True), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.RFC822Name(email)]), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_public_key), False)
            .sign(self._private_key, hashes.SHA256())
        )
        return IssuedCertificate(private_key=key, certificate=certificate)
