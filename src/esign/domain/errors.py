"""Exceptions raised when a business rule is violated.

The HTTP adapter maps each class to an RFC 9457 problem type, so the domain
never needs to know about status codes.
"""


class DomainError(Exception):
    """Base class for all business-rule violations."""


class NotFoundError(DomainError):
    def __init__(self, kind: str, identifier: str) -> None:
        super().__init__(f"{kind} '{identifier}' was not found")
        self.kind = kind
        self.identifier = identifier


class ValidationError(DomainError):
    """Input is well-formed but breaks a business rule (e.g. duplicate signer)."""


class InvalidDocumentError(DomainError):
    """The uploaded file is not a PDF we can sign."""


class DocumentTooLargeError(DomainError):
    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"document is {size} bytes; the limit is {limit} bytes")
        self.size = size
        self.limit = limit


class InvalidTransitionError(DomainError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"envelope cannot move from {current} to {target}")
        self.current = current
        self.target = target


class SigningLinkNotFoundError(DomainError):
    def __init__(self) -> None:
        super().__init__("signing link is unknown")


class SigningLinkExpiredError(DomainError):
    def __init__(self) -> None:
        super().__init__("signing link has expired")


class SigningLinkInactiveError(DomainError):
    """The link was already used, or the envelope is no longer open for signing."""


class ConcurrencyError(DomainError):
    """Another request modified the same data first; the client may retry."""


class IdempotencyKeyReusedError(DomainError):
    def __init__(self) -> None:
        super().__init__("Idempotency-Key was already used with a different request body")
