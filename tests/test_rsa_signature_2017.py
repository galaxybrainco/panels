from datetime import UTC, datetime

import pytest

from actors.services import create_local_actor
from federation.jsonld import canonicalize
from federation.keys import load_actor_keys, rsa_public_key_from_actor
from federation.rsa_signature_2017 import (
    add_rsa_signature_2017,
    verify_rsa_signature_2017,
)

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def _document():
    return {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": "https://panels.test/objects/1",
        "type": "Note",
        "attributedTo": "https://panels.test/actors/alice",
        "content": "Hello world",
    }


def test_canonicalize_is_deterministic():
    first = canonicalize(
        {"@context": ["https://www.w3.org/ns/activitystreams"], "b": 1, "a": 2}
    )
    second = canonicalize(
        {"@context": ["https://www.w3.org/ns/activitystreams"], "a": 2, "b": 1}
    )
    assert first == second


@pytest.mark.django_db
def test_add_and_verify_rsa_signature_2017():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_rsa_signature_2017(
        _document(), keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    signature = signed["signature"]
    assert signature["type"] == "RsaSignature2017"
    assert signature["creator"] == f"{actor.ap_id}#main-key"
    assert "signatureValue" in signature
    assert "https://w3id.org/security/v1" in signed["@context"]
    assert verify_rsa_signature_2017(signed, rsa_public_key_from_actor(actor)) is True


@pytest.mark.django_db
def test_verify_rejects_tampered_content():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_rsa_signature_2017(
        _document(), keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    signed["content"] = "Tampered"
    assert verify_rsa_signature_2017(signed, rsa_public_key_from_actor(actor)) is False


@pytest.mark.django_db
def test_roundtrip_with_security_context_term():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    document = _document()
    document["creator"] = "https://panels.test/actors/alice"
    signed = add_rsa_signature_2017(
        document, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    assert verify_rsa_signature_2017(signed, rsa_public_key_from_actor(actor)) is True


@pytest.mark.django_db
def test_verify_rejects_expired_signature():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_rsa_signature_2017(
        _document(),
        keys.rsa_private_key,
        f"{actor.ap_id}#main-key",
        created="2020-01-01T00:00:00Z",
        expires="2020-01-02T00:00:00Z",
    )
    assert (
        verify_rsa_signature_2017(signed, rsa_public_key_from_actor(actor), now=NOW)
        is False
    )


@pytest.mark.django_db
def test_verify_returns_false_for_malformed_signature_value():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_rsa_signature_2017(
        _document(), keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    signed["signature"]["signatureValue"] = "!!!not-base64!!!"
    assert verify_rsa_signature_2017(signed, rsa_public_key_from_actor(actor)) is False
