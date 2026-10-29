from datetime import UTC, datetime, timedelta

import pytest

from esign.domain.envelopes import (
    ALLOWED_TRANSITIONS,
    Envelope,
    EnvelopeStatus,
    SignerStatus,
)
from esign.domain.errors import (
    InvalidTransitionError,
    SigningLinkExpiredError,
    SigningLinkInactiveError,
    SigningLinkNotFoundError,
    ValidationError,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
TTL = timedelta(hours=72)


def make_envelope(signer_count: int = 2) -> Envelope:
    return Envelope.create(
        title="Contract",
        document_id="doc-1",
        document_sha256="a" * 64,
        signers=[(f"Signer {i}", f"signer{i}@example.com") for i in range(signer_count)],
        now=NOW,
    )


def sent_envelope(signer_count: int = 2) -> Envelope:
    envelope = make_envelope(signer_count)
    envelope.send(NOW)
    envelope.invite_current_signer("hash-0", NOW + TTL)
    return envelope


def status_of(envelope: Envelope) -> EnvelopeStatus:
    # A function call stops mypy from narrowing `envelope.status` across mutations.
    return envelope.status


def test_new_envelope_is_draft_with_waiting_signers() -> None:
    envelope = make_envelope()
    assert envelope.status is EnvelopeStatus.DRAFT
    assert all(s.status is SignerStatus.WAITING for s in envelope.signers)
    assert envelope.current_signer_index is None  # nobody signs a draft


def test_create_requires_signers() -> None:
    with pytest.raises(ValidationError):
        make_envelope(signer_count=0)


def test_create_rejects_duplicate_emails_case_insensitively() -> None:
    with pytest.raises(ValidationError, match="distinct"):
        Envelope.create(
            title="t",
            document_id="d",
            document_sha256="a" * 64,
            signers=[("A", "same@example.com"), ("B", "SAME@example.com")],
            now=NOW,
        )


def test_signers_sign_in_order_until_completed() -> None:
    envelope = sent_envelope(signer_count=3)
    assert envelope.current_signer_index == 0

    envelope.record_signature("hash-0", "b" * 64, NOW)
    assert status_of(envelope) is EnvelopeStatus.PARTIALLY_SIGNED
    assert envelope.current_signer_index == 1
    assert envelope.current_sha256 == "b" * 64

    envelope.invite_current_signer("hash-1", NOW + TTL)
    envelope.record_signature("hash-1", "c" * 64, NOW)
    assert status_of(envelope) is EnvelopeStatus.PARTIALLY_SIGNED  # self-transition

    envelope.invite_current_signer("hash-2", NOW + TTL)
    envelope.record_signature("hash-2", "d" * 64, NOW)
    assert status_of(envelope) is EnvelopeStatus.COMPLETED
    assert envelope.current_signer_index is None
    assert envelope.original_sha256 == "a" * 64


def test_single_signer_goes_straight_to_completed() -> None:
    envelope = sent_envelope(signer_count=1)
    envelope.record_signature("hash-0", "b" * 64, NOW)
    assert envelope.status is EnvelopeStatus.COMPLETED


def test_only_the_current_signer_can_be_invited() -> None:
    envelope = sent_envelope()
    with pytest.raises(ValidationError):
        envelope.invite_current_signer("another", NOW + TTL)  # signer 0 already invited


def test_sending_twice_is_an_invalid_transition() -> None:
    envelope = sent_envelope()
    with pytest.raises(InvalidTransitionError) as caught:
        envelope.send(NOW)
    assert caught.value.current == EnvelopeStatus.SENT
    assert caught.value.target == EnvelopeStatus.SENT


def test_draft_cannot_expire() -> None:
    with pytest.raises(InvalidTransitionError):
        make_envelope().expire(NOW)


@pytest.mark.parametrize(
    "terminal", [EnvelopeStatus.COMPLETED, EnvelopeStatus.DECLINED, EnvelopeStatus.EXPIRED]
)
def test_terminal_states_have_no_exits(terminal: EnvelopeStatus) -> None:
    assert ALLOWED_TRANSITIONS[terminal] == frozenset()


def test_every_status_has_a_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(EnvelopeStatus)


def test_decline_closes_the_envelope() -> None:
    envelope = sent_envelope()
    signer = envelope.decline("hash-0", "wrong amount", NOW)
    assert envelope.status is EnvelopeStatus.DECLINED
    assert signer.status is SignerStatus.DECLINED
    assert signer.decline_reason == "wrong amount"


def test_link_is_one_time() -> None:
    envelope = sent_envelope()
    envelope.record_signature("hash-0", "b" * 64, NOW)
    with pytest.raises(SigningLinkInactiveError):
        envelope.record_signature("hash-0", "c" * 64, NOW)


def test_expired_link_is_rejected() -> None:
    envelope = sent_envelope()
    with pytest.raises(SigningLinkExpiredError):
        envelope.authorize_link("hash-0", NOW + TTL)  # expiry instant is exclusive


def test_unknown_link_is_rejected() -> None:
    with pytest.raises(SigningLinkNotFoundError):
        sent_envelope().authorize_link("nope", NOW)


def test_closed_envelope_reports_inactive_before_expired() -> None:
    envelope = sent_envelope()
    envelope.expire(NOW)
    with pytest.raises(SigningLinkInactiveError):
        envelope.authorize_link("hash-0", NOW + TTL * 2)


def test_transition_updates_timestamp() -> None:
    envelope = make_envelope()
    later = NOW + timedelta(minutes=5)
    envelope.send(later)
    assert envelope.updated_at == later
