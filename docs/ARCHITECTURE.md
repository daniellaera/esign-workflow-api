# Architecture

## Components (ports and adapters)

![Architecture: HTTP adapter, application core with use cases, domain and ports, driven adapters for MongoDB, storage, pyHanko and webhooks](diagrams/architecture.svg)

## Signing flow

![Signing flow: upload, create, send, two sequential signatures, webhook, verify](diagrams/signing-flow.svg)

## Envelope state machine

```mermaid
stateDiagram-v2
    direction LR
    [*] --> DRAFT: create
    DRAFT --> Open: send
    state "Open for signing" as Open {
        direction LR
        [*] --> SENT
        SENT --> PARTIALLY_SIGNED: a signer signs
        PARTIALLY_SIGNED --> PARTIALLY_SIGNED: next signer signs
    }
    Open --> COMPLETED: last signer signs
    Open --> DECLINED: a signer declines
    Open --> EXPIRED: link used after 72 h
```

Any other transition is rejected with `409 invalid-state-transition`.

## Audit hash chain

```mermaid
flowchart LR
    G(["genesis<br/>000…000"])
    E1["seq 1<br/>envelope.created<br/>prev: 000…<br/>hash: a41f…"]
    E2["seq 2<br/>envelope.sent<br/>prev: a41f…<br/>hash: 7c02…"]
    E3["seq 3<br/>signer.invited<br/>prev: 7c02…<br/>hash: e9b5…"]
    EN["seq n<br/>envelope.completed<br/>prev: …<br/>hash: 31d8…"]
    G --> E1 --> E2 --> E3 -.-> EN
```

`hash = SHA-256(canonical JSON of the event + previous_hash)`. Editing, deleting or reordering any event breaks every link after it.

## Webhook delivery

```mermaid
sequenceDiagram
    autonumber
    participant S as SigningService
    participant DB as MongoDB
    participant D as WebhookDispatcher
    participant R as Receiver
    S->>DB: one transaction: COMPLETED + audit event + delivery (PENDING)
    S-)D: dispatch after commit (asyncio task)
    loop up to 4 attempts
        D->>R: POST payload + X-Esign-Timestamp + X-Esign-Signature
        R-->>D: 2xx, error or timeout
        D->>DB: record attempt, status
        Note over D: on failure wait 1 s, 2 s, 4 s
    end
    Note over R: HMAC-SHA256(secret, "timestamp.body"),<br/>constant-time compare, reject old timestamps
```

## In short

- **Domain** (`esign.domain`): pure Python. State machine, tokens, audit chain, HMAC. No framework imports.
- **Application** (`esign.application`): one service per use case. Talks to the outside only through `Protocol` ports.
- **Adapters** (`esign.adapters`): FastAPI in front; MongoDB, local storage, pyHanko and httpx behind.
- **Composition root** (`esign.container`): the only module that knows concrete classes.
- **Transactions**: every state change writes envelope, audit event, outbox and webhook record atomically (MongoDB replica set).
- **Concurrency**: optimistic locking on the envelope `version`; a unique index keeps the audit chain linear.
- **Signing** runs before the transaction. Each revision is an immutable blob keyed by its SHA-256.
- **Signatures**: PAdES, incremental update, visible stamp, short-lived certificate from the TEST CA.
- **Links**: only the current signer has one. Only its SHA-256 is stored. One-time, 72 h.
- **Webhooks**: recorded in the transaction, delivered after commit, every attempt stored.
- **Errors**: RFC 9457 problem details from one mapping table. **Logs**: JSON, request id, no personal data, no tokens.

Details and trade-offs: [ADRs](adr/README.md).
