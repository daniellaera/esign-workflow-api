# 0005. Content-addressed PDF storage and incremental signatures

**Status:** accepted

## Context
Every signature produces a new PDF revision. We need the original, the latest revision, and safety when two requests race.

## Decision
- PDFs are stored under their SHA-256 hash (`BlobStorage.put(sha256, bytes)`), behind a two-method interface implemented on a local volume. An object store would implement the same interface.
- The envelope records `original_sha256` and `current_sha256`; each `signer.signed` audit event records the hash of the revision that signer produced.
- Files are written to a temp file and renamed, so readers never see partial content. Reads re-check the hash.

## Consequences
- Blobs are immutable, so the slow signing step can run outside the database transaction. If a request loses the optimistic-locking race, its blob is simply unreferenced (garbage collection is out of scope).
- Uploading the same PDF twice stores it once.
- Because signatures are incremental, every intermediate revision is also embedded in the final file; pyHanko reports what changed after each signature (`modification_level`).
