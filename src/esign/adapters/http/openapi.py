"""Texts for the OpenAPI document served at /docs."""

from datetime import timedelta

from esign.config import Settings

DESCRIPTION = """
Sequential document signing with real PAdES signatures and a tamper-evident audit trail.

**Signatures come from a TEST CA and are not qualified.**

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
| `urn:esign:problem:rate-limited` | 429 |

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
    {"name": "demo", "description": "Public demo only: read the current signing link."},
    {"name": "development", "description": "Simulated mailbox. Disabled unless configured."},
]


def _duration(value: timedelta) -> str:
    hours = value.total_seconds() / 3600
    if hours >= 1:
        return f"{hours:g} h"
    return f"{value.total_seconds() / 60:g} min"


def description(settings: Settings) -> str:
    """The API description, with a warning banner first when demo mode is on."""
    if not settings.demo_mode or settings.demo_api_key is None:
        return DESCRIPTION
    banner = f"""
> ⚠️ **Public demo.** All data is deleted after {_duration(settings.demo_retention)}.
> **Do not upload real or personal documents.** Signatures come from a TEST CA.

**Demo API key:** `{settings.demo_api_key.get_secret_value()}` (click **Authorize**).
Limits: {settings.max_upload_bytes // (1024 * 1024)} MB per PDF,
{settings.max_signers} signers per envelope,
{settings.rate_limit_per_minute} requests/min and
{settings.envelope_limit_per_hour} new envelopes/hour per IP. No outgoing webhooks.

**Try it: one envelope, two signers**
1. Click **Authorize** and paste the demo key.
2. `POST /documents`: upload a PDF as `file`. Copy `id`, this is your **document_id**.
3. `POST /envelopes`: paste it as `document_id`, keep the two example signers.
   Copy the top-level `id`, this is your **envelope_id**. Use it in every step below.
4. `POST /envelopes/{{envelope_id}}/send`
5. `GET /envelopes/{{envelope_id}}/signing-link`: copy `token`.
6. `POST /sign/{{token}}`: the first signer signs.
7. Repeat steps 5 and 6: the link now belongs to the second signer.
8. `GET /envelopes/{{envelope_id}}/verify`: expect `all_signatures_valid: true`.
   `GET /envelopes/{{envelope_id}}/document` downloads the signed PDF.

---
"""
    return banner + DESCRIPTION
