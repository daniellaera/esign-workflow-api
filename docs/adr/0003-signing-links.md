# 0003. Signing links: sequential issuance, hashed tokens, lazy expiry

**Status:** accepted

## Context
Signers have no account; the link in their (simulated) email is their only credential. Signing is sequential.

## Decision
- **Sequential issuance.** Only the signer whose turn it is holds a link. The next link is created in the same transaction that records the previous signature. An early signer therefore cannot be "skipped", and a leaked link for signer 3 cannot exist before signers 1 and 2 have signed.
- **Token format.** `secrets.token_urlsafe(32)`: 256 bits of randomness.
- **Hashed at rest.** Only `sha256(token)` is stored, in the signer sub-document. A database dump does not yield usable links. SHA-256 rather than bcrypt/argon2 because the input is a high-entropy random value, not a password, and the hash must be deterministic so the envelope can be looked up by it (indexed field `signers.token_hash`).
- **One-time.** After signing or declining, the signer's status changes and the link is reported as inactive (HTTP 410). The hash is kept so a reused link gets a clear error rather than "not found".
- **72h expiry, applied lazily.** When someone uses an expired link, the envelope transitions to `EXPIRED` and the request fails with 410. There is no background sweeper.

## Consequences
- An envelope nobody touches stays `SENT` past its deadline until someone uses the link. A production system would add a periodic job (e.g. a Kubernetes CronJob) that expires overdue envelopes and notifies the sender.
- The outbox stores the full link because it stands in for the mail provider. Real deployments would hand the link to the mail provider and not persist it; `ESIGN_EXPOSE_OUTBOX` exists only for local demos.
- Responses on `/sign/*` carry `Cache-Control: no-store` and `Referrer-Policy: no-referrer` to keep the token out of caches and Referer headers.
