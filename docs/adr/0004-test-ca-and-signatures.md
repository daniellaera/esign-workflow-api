# 0004. Test CA, per-signer short-lived certificates, validation time

**Status:** accepted

## Context
The final PDF must carry one real PAdES signature per signer. Remote-signing services (the model behind most e-signature platforms) generate or unlock a key per signing operation and use a short-lived certificate naming the signer.

## Decision
- A local CA, created with `cryptography` on first start and persisted to the data volume, named **"TEST CA – NOT QUALIFIED"**.
- For each signature: a fresh EC P-256 key and a certificate valid for one hour (configurable) with the signer's name and email in the subject, key usage `digitalSignature` + `nonRepudiation`. The key is used once and discarded.
- pyHanko signs with subfilter `ETSI.CAdES.detached` (PAdES baseline B-B), SHA-256, as an **incremental update**, so earlier signatures remain valid.
- Verification builds a pyHanko `ValidationContext` with the test CA as the only trust root, no network fetching, revocation checking in soft-fail mode.
- **Validation time = the signing time claimed in the signature.** Validating "now" would mark every signature invalid an hour after signing, when its certificate expires.

## Consequences
- The claimed signing time is self-asserted. Without a trusted timestamp (PAdES-B-T) or long-term validation material (B-LT/B-LTA), a verifier has to trust our clock. pyHanko supports RFC 3161 timestamps; adding a TSA is the natural next step.
- No CRL/OCSP: the certificates are too short-lived for revocation to matter in this POC, and there is nothing to revoke them with.
- The CA key is a file. In any real system it lives in an HSM operated by a qualified trust service provider. See the README section "Not a qualified signature".
- Running several API replicas would need the CA files on shared storage (or, realistically, an external signing service).
