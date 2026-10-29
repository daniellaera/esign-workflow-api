"""Application factory. Run with ``uvicorn --factory esign.main:create_app``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from esign.adapters.http.middleware import RequestContextMiddleware
from esign.adapters.http.problems import install_problem_handlers
from esign.adapters.http.routes import dev, documents, envelopes, health, signing, trust
from esign.application.ports import Clock
from esign.config import Settings
from esign.container import Container
from esign.domain.common import utc_now
from esign.logs import configure_logging


def create_app(settings: Settings | None = None, *, clock: Clock = utc_now) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await Container.start(settings, clock)
        app.state.container = container
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(
        title="esign-workflow-api",
        version="0.1.0",
        summary="Sequential document signing with PAdES signatures and a tamper-evident audit.",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    install_problem_handlers(app)

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(envelopes.router)
    app.include_router(signing.router)
    app.include_router(trust.router)
    if settings.expose_outbox:
        app.include_router(dev.router)
    return app
