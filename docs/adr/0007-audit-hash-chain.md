# 0007. Audit trail as a per-envelope hash chain

**Status:** accepted

## Context
The audit trail is evidence. It must be append-only, and tampering must be detectable.

## Decision
- Collection `audit_events`; the repository has no update or delete method.
- Each event stores `previous_hash` and `hash = SHA-256(canonical JSON of envelope_id, sequence, type, occurred_at, actor, data, previous_hash)`. The first event links to 64 zeros.
- Canonical JSON: sorted keys, no whitespace, UTF-8, timestamps as UTC ISO 8601 with millisecond precision. Timestamps are truncated to milliseconds when created because MongoDB stores milliseconds; otherwise a value read back would not hash the same.
- One chain per envelope, not one global chain: no global write bottleneck, and an envelope's evidence can be exported on its own.
- A unique index on `(envelope_id, sequence)` makes it impossible for two concurrent writers to fork the chain.
- `GET /envelopes/{id}/audit` recomputes the chain and reports `chain_valid` and the first broken sequence number.
- Signer events record IP address and User-Agent as evidence.

## Consequences
- This **detects** edits, deletions and reordering. It does not **prevent** someone with database write access from rewriting the whole chain consistently. Closing that gap means anchoring the latest hash outside the database: e.g. in an RFC 3161 timestamp, a WORM store, or inside the signed PDF itself.
- The audit trail holds personal data (emails, IPs) by design, so its retention would need a policy under nFADP/GDPR.
