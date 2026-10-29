import hashlib

from esign.domain.tokens import SigningToken, hash_token


def test_generated_tokens_are_unique_and_url_safe() -> None:
    tokens = {SigningToken.generate().value for _ in range(100)}
    assert len(tokens) == 100
    for token in tokens:
        assert len(token) >= 43  # 32 random bytes, base64url without padding
        assert all(c.isalnum() or c in "-_" for c in token)


def test_only_the_hash_is_meant_to_be_stored() -> None:
    token = SigningToken.generate()
    assert token.hash == hashlib.sha256(token.value.encode()).hexdigest()
    assert token.value not in token.hash


def test_hash_is_deterministic_so_it_can_be_looked_up() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")


def test_hash_tolerates_non_ascii_input() -> None:
    # A garbage token from the URL must not crash the lookup.
    assert len(hash_token("tökén")) == 64
