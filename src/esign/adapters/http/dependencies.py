"""FastAPI dependencies that hand route functions their collaborators."""

from typing import Annotated

from fastapi import Depends, Request

from esign.application.signing import ClientInfo
from esign.container import Container

USER_AGENT_MAX_LENGTH = 256


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_client_info(request: Request) -> ClientInfo:
    # Behind a reverse proxy, list it in ESIGN_TRUSTED_PROXIES so this is the real client.
    user_agent = request.headers.get("user-agent")
    return ClientInfo(
        ip=request.client.host if request.client else None,
        user_agent=user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None,
    )


ContainerDep = Annotated[Container, Depends(get_container)]
ClientInfoDep = Annotated[ClientInfo, Depends(get_client_info)]
