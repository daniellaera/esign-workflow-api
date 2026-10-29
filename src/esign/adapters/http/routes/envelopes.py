from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status

from esign.adapters.http.dependencies import ContainerDep
from esign.adapters.http.problems import ProblemDetails
from esign.adapters.http.schemas import (
    AuditTrailOut,
    EnvelopeIn,
    EnvelopeOut,
    VerificationOut,
    WebhookDeliveryOut,
)
from esign.adapters.http.security import require_api_key
from esign.application.envelopes import NewEnvelope
from esign.application.ports import SignerIdentity

router = APIRouter(
    prefix="/envelopes",
    tags=["envelopes"],
    dependencies=[Depends(require_api_key)],
    responses={"4XX": {"model": ProblemDetails, "description": "RFC 9457 problem details"}},
)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create an envelope")
async def create_envelope(
    body: EnvelopeIn,
    response: Response,
    container: ContainerDep,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> EnvelopeOut:
    """Creates a `DRAFT` envelope. Signers sign in the order given.

    Send an `Idempotency-Key` header to make retries safe: the same key and body
    return the original envelope (header `Idempotent-Replayed: true`)."""
    request = NewEnvelope(
        title=body.title,
        document_id=body.document_id,
        signers=tuple(SignerIdentity(name=s.name, email=str(s.email)) for s in body.signers),
    )
    result = await container.envelopes.create(request, idempotency_key)
    response.headers["Location"] = f"/envelopes/{result.envelope.id}"
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return EnvelopeOut.from_domain(result.envelope)


@router.get("/{envelope_id}", summary="Get an envelope")
async def get_envelope(envelope_id: str, container: ContainerDep) -> EnvelopeOut:
    """Status of the envelope and of each signer."""
    return EnvelopeOut.from_domain(await container.envelopes.get(envelope_id))


@router.post("/{envelope_id}/send", summary="Send to the first signer")
async def send_envelope(envelope_id: str, container: ContainerDep) -> EnvelopeOut:
    """`DRAFT` → `SENT`. Issues a one-time link (valid 72 h) to the first signer."""
    return EnvelopeOut.from_domain(await container.envelopes.send(envelope_id))


@router.get(
    "/{envelope_id}/document",
    summary="Download the current PDF",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The current PDF"}},
)
async def get_document(envelope_id: str, container: ContainerDep) -> Response:
    """The latest revision: the original plus one incremental update per signature."""
    envelope, pdf = await container.envelopes.current_document(envelope_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="envelope-{envelope.id}.pdf"',
            "ETag": f'"{envelope.current_sha256}"',
        },
    )


@router.get("/{envelope_id}/verify", summary="Verify all signatures")
async def verify_envelope(envelope_id: str, container: ContainerDep) -> VerificationOut:
    """Validate every PAdES signature in the current PDF against the test CA."""
    return VerificationOut.from_report(await container.envelopes.verify(envelope_id))


@router.get("/{envelope_id}/audit", summary="Get the audit trail")
async def get_audit_trail(envelope_id: str, container: ContainerDep) -> AuditTrailOut:
    """All audit events, plus whether the hash chain still verifies."""
    trail = await container.envelopes.audit_trail(envelope_id)
    return AuditTrailOut.from_trail(envelope_id, trail)


@router.get("/{envelope_id}/webhook-deliveries", summary="List webhook deliveries")
async def list_webhook_deliveries(
    envelope_id: str, container: ContainerDep
) -> list[WebhookDeliveryOut]:
    """Each delivery with every attempt: status code or error."""
    deliveries = await container.envelopes.webhook_deliveries(envelope_id)
    return [WebhookDeliveryOut.from_domain(d) for d in deliveries]
