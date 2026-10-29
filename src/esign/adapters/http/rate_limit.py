"""Per-client-IP rate limiting for the public demo.

Fixed windows counted in memory: simple and enough for a single instance.
Several replicas would need a shared counter (e.g. Redis) instead.

The client IP is ``scope["client"]``. Behind a reverse proxy, the proxy
headers middleware rewrites it from X-Forwarded-For, but only for requests
coming from a trusted proxy address (``ESIGN_TRUSTED_PROXIES``).
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from esign.adapters.http.problems import TYPE_PREFIX, problem_response

# Probes must never be throttled, or the orchestrator would restart a healthy app.
EXEMPT_PREFIXES = ("/health",)
# Bound the counter table; expired windows are pruned once it grows past this.
_PRUNE_THRESHOLD = 10_000


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    limit: int
    window_seconds: int
    method: str | None = None  # None: any method
    path: str | None = None  # None: any path

    def matches(self, method: str, path: str) -> bool:
        return (self.method is None or self.method == method) and (
            self.path is None or self.path == path
        )


class FixedWindowLimiter:
    def __init__(self, now: Callable[[], float]) -> None:
        self._now = now
        # (key, window start) -> number of hits in that window
        self._counts: dict[tuple[str, int], int] = {}

    def hit(self, key: str, limit: int, window_seconds: int) -> int | None:
        """Count one hit. Returns seconds until the window resets if over the limit."""
        now = self._now()
        window_start = int(now // window_seconds) * window_seconds
        if len(self._counts) > _PRUNE_THRESHOLD:
            self._prune(now)
        count = self._counts.get((key, window_start), 0) + 1
        self._counts[(key, window_start)] = count
        if count <= limit:
            return None
        return max(1, math.ceil(window_start + window_seconds - now))

    def _prune(self, now: float) -> None:
        # Windows are at most an hour long; anything older is expired.
        self._counts = {
            (key, start): count
            for (key, start), count in self._counts.items()
            if start + 3600 > now
        }


class RateLimitMiddleware:
    def __init__(
        self, app: ASGIApp, *, rules: Sequence[RateLimitRule], limiter: FixedWindowLimiter
    ) -> None:
        self.app = app
        self.rules = rules
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        for rule in self.rules:
            if not rule.matches(scope["method"], scope["path"]):
                continue
            key = f"{rule.name}:{client_ip}"
            retry_after = self.limiter.hit(key, rule.limit, rule.window_seconds)
            if retry_after is not None:
                response = problem_response(
                    Request(scope),
                    status=429,
                    title="Too Many Requests",
                    detail=f"{rule.name}: {rule.limit} per {rule.window_seconds} s exceeded",
                    problem_type=TYPE_PREFIX + "rate-limited",
                    headers={"Retry-After": str(retry_after)},
                    retry_after_seconds=retry_after,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def demo_rules(*, per_minute: int, envelopes_per_hour: int) -> list[RateLimitRule]:
    return [
        RateLimitRule("requests", per_minute, 60),
        RateLimitRule("envelopes", envelopes_per_hour, 3600, method="POST", path="/envelopes"),
    ]
