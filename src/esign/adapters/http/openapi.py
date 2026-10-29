"""Texts for the OpenAPI document served at /docs."""

DESCRIPTION = """
Sequential document signing with real PAdES signatures and a tamper-evident audit trail.

**Signatures come from a TEST CA and are not qualified.**

## Flow
1. `POST /documents`: upload a PDF.
2. `POST /envelopes`: document plus signers, in signing order.
3. `POST /envelopes/{id}/send`: the first signer gets a one-time link.
4. Each signer: `GET /sign/{token}`, then `POST /sign/{token}` (or `/decline`).
5. `GET /envelopes/{id}/verify` and `GET /envelopes/{id}/audit`.

## Authentication
Management endpoints need the `X-API-Key` header (use **Authorize**).
Signing endpoints need nothing but the token in the link.

## Errors
RFC 9457 problem details (`application/problem+json`). Domain errors have a stable `type`:

| type | status |
|---|---|
| `urn:esign:problem:invalid-request` | 422 |
| `urn:esign:problem:validation-failed` | 422 |
| `urn:esign:problem:invalid-document` | 422 |
| `urn:esign:problem:document-too-large` | 413 |
| `urn:esign:problem:not-found` | 404 |
| `urn:esign:problem:invalid-state-transition` | 409 |
| `urn:esign:problem:concurrent-modification` | 409 |
| `urn:esign:problem:idempotency-key-reused` | 422 |
| `urn:esign:problem:signing-link-not-found` | 404 |
| `urn:esign:problem:signing-link-expired` | 410 |
| `urn:esign:problem:signing-link-inactive` | 410 |

## Webhooks
On `envelope.completed` and `envelope.declined`, a POST to `ESIGN_WEBHOOK_URL` with headers
`X-Esign-Event`, `X-Esign-Delivery`, `X-Esign-Timestamp` and
`X-Esign-Signature: sha256=HMAC_SHA256(secret, "{timestamp}.{raw body}")`.
Up to 3 retries with exponential backoff.
"""

TAGS = [
    {"name": "documents", "description": "Upload PDFs to sign."},
    {"name": "envelopes", "description": "Create, send, download, verify and audit envelopes."},
    {"name": "signing", "description": "Signer-facing. The token in the link is the credential."},
    {"name": "trust", "description": "The test CA certificate, for external validators."},
    {"name": "health", "description": "Kubernetes-style probes."},
    {"name": "development", "description": "Simulated mailbox. Disabled unless configured."},
]
