import pytest

from actors.services import create_local_actor
from federation.jsonld import canonicalize
from federation.keys import load_actor_keys, rsa_public_key_from_actor
from federation.rsa_signature_2017 import (
    add_rsa_signature_2017,
    verify_rsa_signature_2017,
)


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
