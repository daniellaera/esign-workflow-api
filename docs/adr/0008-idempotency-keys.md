# 0008. Idempotency keys for envelope creation

**Status:** accepted

## Context
A client that times out on `POST /envelopes` does not know whether the envelope was created. Retrying blindly creates duplicates that would each send emails.

## Decision
Follow the IETF `Idempotency-Key` header draft:
- The key and a SHA-256 fingerprint of the request body are stored with the created envelope id, **in the same transaction** as the envelope.
- Same key + same body → the original envelope, `201`, plus `Idempotent-Replayed: true`.
- Same key + different body → `422 idempotency-key-reused`.
- Two concurrent requests with the same key → the second insert hits the unique `_id` and fails with `409`; the client retries and gets the replay.
- A TTL index removes keys after 24 hours.

## Consequences
- Keys are global, not per client, because there is a single API key. With multiple clients the key would be scoped by client id.
- Only `POST /envelopes` supports it. `send` is naturally protected by the state machine (a second send is a 409).
