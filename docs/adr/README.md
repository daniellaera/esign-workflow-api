# Architecture Decision Records

Short records of decisions that are not obvious from the code. Format: context, decision, consequences.

| # | Decision |
|---|----------|
| [0001](0001-lightweight-ports-and-adapters.md) | Lightweight ports and adapters |
| [0002](0002-mongodb-transactions.md) | MongoDB transactions on a single-node replica set |
| [0003](0003-signing-links.md) | Signing links: sequential issuance, hashed tokens, lazy expiry |
| [0004](0004-test-ca-and-signatures.md) | Test CA, per-signer short-lived certificates, validation time |
| [0005](0005-content-addressed-storage.md) | Content-addressed PDF storage and incremental signatures |
| [0006](0006-webhook-delivery.md) | Webhooks: record in the transaction, deliver in-process |
| [0007](0007-audit-hash-chain.md) | Audit trail as a per-envelope hash chain |
| [0008](0008-idempotency-keys.md) | Idempotency keys for envelope creation |
| [0009](0009-logging-without-secrets.md) | JSON logging without personal data or secrets |
