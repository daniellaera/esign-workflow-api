"""One-time signing tokens.

The raw token travels only inside the signing link. We persist its SHA-256
hash, so a database leak does not reveal usable links. A fast hash is fine
here (unlike passwords) because the token has 256 bits of entropy, and it must
be deterministic so we can look the envelope up by it.
"""

import hashlib
import secrets
from dataclasses import dataclass

TOKEN_BYTES = 32


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", errors="replace")).hexdigest()


@dataclass(frozen=True)
class SigningToken:
    value: str
    hash: str

    @classmethod
    def generate(cls) -> "SigningToken":
        value = secrets.token_urlsafe(TOKEN_BYTES)
        return cls(value=value, hash=hash_token(value))
