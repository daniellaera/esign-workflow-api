# 0010. Public demo mode

**Status:** accepted

## Context
The API runs as a public demo. Strangers will upload files and create envelopes, so the demo must not keep data, must be cheap to abuse-proof, and must be usable from Swagger alone without an email inbox.

## Decision
One setting, `ESIGN_DEMO_MODE`, off by default, switches on:

- **Retention.** TTL indexes (named `retention_ttl`) on documents, envelopes, audit events, outbox and webhook records delete them after `ESIGN_DEMO_RETENTION` (24 h). A background task deletes PDF files older than that. The indexes are dropped and recreated at every start, so turning demo mode off removes them and never leaves a data-deleting index behind.
- **Limits.** 2 MB uploads and 5 signers per envelope, applied in the settings validator.
- **Rate limiting.** Per client IP, fixed windows in memory: 30 requests/minute and 10 new envelopes/hour. Over the limit: `429` problem details with `Retry-After`. `/health` is exempt.
- **Client IP.** `X-Forwarded-For` is honoured only from addresses in `ESIGN_TRUSTED_PROXIES` (uvicorn's proxy-headers middleware, mounted by the app). Anyone else's header is ignored, so a client cannot dodge the limiter by spoofing it.
- **Public API key.** `ESIGN_DEMO_API_KEY` is accepted next to the private operator key.
- **Signing links via the API.** `GET /envelopes/{id}/signing-link` returns the current signer's link. In production, a link exists only in the signer's email; this endpoint is mounted only in demo mode.
- **No outgoing webhooks**, so the demo cannot be used to send requests to arbitrary hosts.
- **Swagger banner** stating that data is deleted and real documents must not be uploaded.

## Consequences
- The in-memory limiter is per process; several replicas would need a shared store (e.g. Redis).
- A purged file can still be referenced by an envelope created later from the same upload; reading it returns `404` instead of an error.
- MongoDB's TTL monitor runs once a minute, so records live up to about a minute past the retention period.
