# 0002. MongoDB transactions on a single-node replica set

**Status:** accepted

## Context
One signing step writes to several collections: the envelope (status, current document hash, next signer's link), the audit trail, the outbox (next signer's "email") and possibly a webhook delivery record. If the process dies between those writes, we get an envelope that moved on without an audit entry, or an invited signer who never got a link.

Alternatives considered:
1. Embed everything in the envelope document (single-document atomicity). The audit trail and outbox would grow inside the envelope and would be awkward to query.
2. Order the writes carefully and accept gaps. Hard to reason about, and the audit trail is the one place where gaps matter most.
3. Multi-document transactions. They need a replica set.

## Decision
Use transactions. `Database.transaction()` yields repositories bound to a session; everything inside commits or aborts together. MongoDB runs as a single-node replica set in docker-compose and in tests (testcontainers), which costs nothing extra.

Concurrency control on top of that:
- Envelopes carry a `version` field; `update()` is a compare-and-set on it (optimistic locking). A stale write raises `ConcurrencyError` → HTTP 409.
- Write conflicts between transactions (MongoDB error label `TransientTransactionError`) are mapped to the same `ConcurrencyError`.

## Consequences
- The client must retry on 409. We do not retry server-side, to keep each request's behaviour obvious.
- Slow work (PDF signing) happens *before* the transaction opens, so transactions stay short.
- Production would use a three-node replica set (or Atlas); the code does not change.
- The version is **MongoDB 7.0**. MongoDB 8.x refuses to start on Linux kernels 6.19 and newer ([SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912), a TCMalloc/rseq incompatibility), and GitHub's runners use such a kernel. Nothing here needs 8.x features; move up once MongoDB ships a fix.
