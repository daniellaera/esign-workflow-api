"""Uploading PDFs and loading them back by hash."""

from esign.application.ports import BlobStorage, Clock, Database, SignatureService
from esign.domain.common import new_id, sha256_hex
from esign.domain.documents import Document, check_pdf_upload
from esign.domain.errors import NotFoundError


class DocumentService:
    def __init__(
        self,
        db: Database,
        storage: BlobStorage,
        signatures: SignatureService,
        *,
        max_upload_bytes: int,
        clock: Clock,
    ) -> None:
        self._db = db
        self._storage = storage
        self._signatures = signatures
        self._max_upload_bytes = max_upload_bytes
        self._clock = clock

    async def upload(self, filename: str, data: bytes) -> Document:
        check_pdf_upload(data, self._max_upload_bytes)
        # Parse it now, so a broken PDF fails at upload rather than at signing time.
        await self._signatures.ensure_signable(data)
        digest = sha256_hex(data)
        await self._storage.put(digest, data)
        document = Document(
            id=new_id(),
            filename=filename,
            size_bytes=len(data),
            sha256=digest,
            uploaded_at=self._clock(),
        )
        await self._db.documents.add(document)
        return document


async def load_pdf(storage: BlobStorage, sha256: str) -> bytes:
    """Read a stored PDF and check it still matches the hash it is stored under."""
    try:
        data = await storage.get(sha256)
    except FileNotFoundError as exc:
        # Only expected in demo mode, where old files are purged.
        raise NotFoundError("document file", sha256) from exc
    if sha256_hex(data) != sha256:
        raise RuntimeError(f"stored document {sha256} is corrupted")
    return data
