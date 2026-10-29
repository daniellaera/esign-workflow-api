# 0009. JSON logging without personal data or secrets

**Status:** accepted

## Context
Logs end up in shared systems with broad access and long retention. Signing links are bearer credentials, and signer names and emails are personal data.

## Decision
- Standard library `logging` with a small JSON formatter (no extra dependency). Fields passed via `extra=` become top-level keys.
- A request id (from `X-Request-ID` if well-formed, otherwise generated) is stored in a `ContextVar`, attached to every log line and returned in the response.
- The access log records the **route template** (`/sign/{token}`), never the raw path. Uvicorn's own access log is disabled because it would log the token.
- Application logs carry ids (envelope, delivery, signer index), never names or emails.
- The signing link is logged only when `ESIGN_LOG_SIGNING_LINKS=true`. The brief asked for links to be logged; that is a development convenience, so it is off by default and on in docker-compose.
- Validation errors are returned without echoing the submitted values.

## Consequences
- Debugging a specific signer's problem means correlating through envelope id and the audit trail, which is access-controlled, instead of grepping logs for an email. That is intended.
