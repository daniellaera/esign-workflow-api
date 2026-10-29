"""Demo-mode only: lets the API-key holder fetch the current signing link,
so the whole flow can be tried from Swagger without an email inbox."""

from fastapi import APIRouter, Depends

from esign.adapters.http.dependencies import ContainerDep
from esign.adapters.http.problems import ProblemDetails
from esign.adapters.http.schemas import SigningLinkOut
from esign.adapters.http.security import require_api_key

router = APIRouter(
    prefix="/envelopes",
    tags=["demo"],
    dependencies=[Depends(require_api_key)],
    responses={"4XX": {"model": ProblemDetails, "description": "RFC 9457 problem details"}},
)


@router.get("/{envelope_id}/signing-link", summary="Get the current signing link (demo only)")
async def current_signing_link(envelope_id: str, container: ContainerDep) -> SigningLinkOut:
    """The link of the signer whose turn it is. Use its `token` with `POST /sign/{token}`.

    **Demo only.** In production the link is only ever sent to the signer."""
    return SigningLinkOut.from_link(await container.envelopes.current_signing_link(envelope_id))
