"""Signer-facing endpoints. The token in the path is the only credential."""

from typing import Annotated

from fastapi import APIRouter, Body, Path, Response

from esign.adapters.http.dependencies import ClientInfoDep, ContainerDep
from esign.adapters.http.problems import ProblemDetails
from esign.adapters.http.schemas import DeclineIn, SigningViewOut

router = APIRouter(
    prefix="/sign",
    tags=["signing"],
    responses={"4XX": {"model": ProblemDetails, "description": "RFC 9457 problem details"}},
)

Token = Annotated[str, Path(min_length=16, max_length=128)]

# The URL is a credential: keep it out of caches and Referer headers.
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _private(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


@router.get("/{token}")
async def view(
    token: Token, response: Response, container: ContainerDep, client: ClientInfoDep
) -> SigningViewOut:
    """What the signer is asked to sign, and where they are in the signing order."""
    _private(response)
    return SigningViewOut.from_session(await container.signing.open(token, client))


@router.get(
    "/{token}/document",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The PDF to sign"}},
)
async def document(token: Token, container: ContainerDep) -> Response:
    pdf = await container.signing.document(token)
    return Response(content=pdf, media_type="application/pdf", headers=_PRIVATE_HEADERS)


@router.post("/{token}")
async def sign(
    token: Token, response: Response, container: ContainerDep, client: ClientInfoDep
) -> SigningViewOut:
    """Apply this signer's PAdES signature. The link cannot be used again."""
    _private(response)
    return SigningViewOut.from_session(await container.signing.sign(token, client))


@router.post("/{token}/decline")
async def decline(
    token: Token,
    response: Response,
    container: ContainerDep,
    client: ClientInfoDep,
    body: Annotated[DeclineIn | None, Body()] = None,
) -> SigningViewOut:
    """Decline to sign. The whole envelope becomes DECLINED."""
    _private(response)
    reason = body.reason if body else None
    return SigningViewOut.from_session(await container.signing.decline(token, reason, client))
