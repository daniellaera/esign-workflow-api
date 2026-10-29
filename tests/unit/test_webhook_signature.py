import hashlib
import hmac

from esign.domain.webhooks import compute_signature, verify_signature

SECRET = b"whsec-test"
BODY = b'{"event":"envelope.completed"}'


def test_signature_is_hmac_sha256_over_timestamp_and_body() -> None:
    expected = hmac.new(SECRET, b"1700000000." + BODY, hashlib.sha256).hexdigest()
    assert compute_signature(SECRET, 1700000000, BODY) == f"sha256={expected}"


def test_valid_signature_verifies() -> None:
    signature = compute_signature(SECRET, 1000, BODY)
    assert verify_signature(SECRET, 1000, BODY, signature, now=1010)


def test_tampered_body_fails() -> None:
    signature = compute_signature(SECRET, 1000, BODY)
    assert not verify_signature(SECRET, 1000, BODY + b" ", signature, now=1000)


def test_wrong_secret_fails() -> None:
    signature = compute_signature(b"other", 1000, BODY)
    assert not verify_signature(SECRET, 1000, BODY, signature, now=1000)


def test_replayed_old_request_fails() -> None:
    signature = compute_signature(SECRET, 1000, BODY)
    assert not verify_signature(SECRET, 1000, BODY, signature, now=1000 + 301)


def test_timestamp_is_bound_to_the_signature() -> None:
    signature = compute_signature(SECRET, 1000, BODY)
    assert not verify_signature(SECRET, 1001, BODY, signature, now=1001)
