import pytest
import requests

from actors.services import create_local_actor
from federation.keys import load_actor_keys, rsa_public_key_from_actor
from federation.rfc9421 import sign_rfc9421, verify_rfc9421


def _prepared(url):
    request = requests.Request("POST", url)
    prepared = request.prepare()
    prepared.headers["Content-Digest"] = (
        "sha-256=:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=:"
    )
    return prepared


@pytest.mark.django_db
def test_rfc9421_sign_verify_roundtrip():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox")
    signed = sign_rfc9421(message, keys.rsa_private_key, f"{actor.ap_id}#main-key")
    assert "Signature-Input" in signed.headers
    assert "Signature" in signed.headers
    assert (
        verify_rfc9421(signed, lambda key_id: rsa_public_key_from_actor(actor)) is True
    )


@pytest.mark.django_db
def test_rfc9421_rejects_unknown_key():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox")
    signed = sign_rfc9421(message, keys.rsa_private_key, f"{actor.ap_id}#main-key")
    assert verify_rfc9421(signed, lambda key_id: None) is False


@pytest.mark.django_db
def test_rfc9421_rejects_tampered_target():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox")
    signed = sign_rfc9421(message, keys.rsa_private_key, f"{actor.ap_id}#main-key")
    signed.url = "https://panels.test/actors/alice/outbox"
    assert (
        verify_rfc9421(signed, lambda key_id: rsa_public_key_from_actor(actor)) is False
    )
