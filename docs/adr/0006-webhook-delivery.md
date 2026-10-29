# 0006. Webhooks: record in the transaction, deliver in-process

**Status:** accepted

## Context
On `COMPLETED` and `DECLINED` we POST to a configured URL, with an HMAC-SHA256 signature, three retries and exponential backoff, recording every attempt.

## Decision
- The `webhook_deliveries` record is inserted **in the same transaction** as the state change. We never announce something that was rolled back.
- After commit, an `asyncio` task delivers it: 1 attempt + 3 retries, waiting 1s, 2s, 4s (base configurable). Each attempt (status code or transport error) is `$push`ed onto the record, and the record ends as `DELIVERED` or `FAILED`.
- Signature: `X-Esign-Signature: sha256=HMAC(secret, "{timestamp}.{body}")` with `X-Esign-Timestamp`. Binding the timestamp lets receivers reject replays. `X-Esign-Delivery` gives receivers an id for de-duplication.
- The payload carries ids, status and the document hash, but no personal data; receivers call the API for details.
- On shutdown, in-flight deliveries get a 10-second grace period.

## Consequences
- If the process dies mid-retry, the record stays `PENDING` and nothing resumes it. The fix is a worker that polls `PENDING` deliveries (a transactional outbox relay), which would also allow multiple replicas without duplicate sends. Out of scope for a POC; the data model already supports it.
- Delivery is at-least-once from the receiver's point of view (a timeout after the receiver processed the request triggers a retry), hence the delivery id.
