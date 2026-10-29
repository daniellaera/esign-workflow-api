"""Application factory. Run with ``uvicorn --factory esign.main:create_app``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from esign.adapters.http.middleware import RequestContextMiddleware
from esign.adapters.http.openapi import TAGS, description
from esign.adapters.http.problems import install_problem_handlers
from esign.adapters.http.rate_limit import FixedWindowLimiter, RateLimitMiddleware, demo_rules
from esign.adapters.http.routes import demo, dev, documents, envelopes, health, signing, trust
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
        description=description(settings),
        openapi_tags=TAGS,
        license_info={"name": "MIT", "identifier": "MIT"},
        lifespan=lifespan,
    )
    # Added innermost first: requests pass proxy headers -> request context -> rate limit.
    if settings.demo_mode:
        app.add_middleware(
            RateLimitMiddleware,
            rules=demo_rules(
                per_minute=settings.rate_limit_per_minute,
                envelopes_per_hour=settings.envelope_limit_per_hour,
            ),
            limiter=FixedWindowLimiter(now=lambda: clock().timestamp()),
        )
    app.add_middleware(RequestContextMiddleware)
    if settings.trusted_proxies:
        # Only these peers may set the client address via X-Forwarded-For.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxies)
    install_problem_handlers(app)

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(envelopes.router)
    app.include_router(signing.router)
    app.include_router(trust.router)
    if settings.expose_outbox:
        app.include_router(dev.router)
    if settings.demo_mode:
        app.include_router(demo.router)
    return app
