from pathlib import PurePath

from fastapi import APIRouter, Depends, UploadFile, status

from esign.adapters.http.dependencies import ContainerDep
from esign.adapters.http.problems import ProblemDetails
from esign.adapters.http.schemas import DocumentOut
from esign.adapters.http.security import require_api_key
from esign.domain.errors import DocumentTooLargeError

router = APIRouter(
    tags=["documents"],
    dependencies=[Depends(require_api_key)],
    responses={"4XX": {"model": ProblemDetails, "description": "RFC 9457 problem details"}},
)


@router.post("/documents", status_code=status.HTTP_201_CREATED, summary="Upload a PDF")
async def upload_document(file: UploadFile, container: ContainerDep) -> DocumentOut:
    """Multipart field `file`, max 10 MB. Returns the document id and its SHA-256."""
    limit = container.settings.max_upload_bytes
    # Starlette has already spooled the upload to a temp file, so memory is
    # not at risk; a reverse proxy should still cap the body size upstream.
    if file.size is not None and file.size > limit:
        raise DocumentTooLargeError(file.size, limit)
    data = await file.read(limit + 1)
    filename = PurePath(file.filename or "").name[:255] or "document.pdf"
    document = await container.documents.upload(filename, data)
    return DocumentOut.from_domain(document)
