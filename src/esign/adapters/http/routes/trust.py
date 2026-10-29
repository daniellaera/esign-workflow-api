from fastapi import APIRouter, Response

from esign.adapters.http.dependencies import ContainerDep

router = APIRouter(prefix="/trust", tags=["trust"])


@router.get(
    "/test-ca.pem",
    summary="Test CA certificate (PEM)",
    response_class=Response,
    responses={200: {"content": {"application/x-pem-file": {}}}},
)
async def test_ca_certificate(container: ContainerDep) -> Response:
    """The TEST CA root certificate, for verifying signatures with external tools."""
    return Response(
        content=container.signatures.trust_anchor_pem(), media_type="application/x-pem-file"
    )
