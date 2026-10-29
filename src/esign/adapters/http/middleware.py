"""Request id, access logging and the last-resort error handler.

Written as plain ASGI middleware rather than ``@app.middleware("http")``:
it streams responses untouched and keeps context variables intact.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from esign.adapters.http.problems import internal_error_response
from esign.logs import request_id_var

logger = logging.getLogger("esign.access")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller-supplied id only if it is short and harmless to log.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Request(scope).headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("unhandled error")
            if response_started:
                raise
            response = internal_error_response(Request(scope))
            await response(scope, receive, send_with_request_id)
        finally:
            # Log the route template (/sign/{token}), never the raw path: it holds secrets.
            route = scope.get("route")
            logger.info(
                "request completed",
                extra={
                    "method": scope["method"],
                    "route": getattr(route, "path", "<unmatched>"),
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_id_var.reset(token)
