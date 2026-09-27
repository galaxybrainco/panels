import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from actors import crypto
from actors.services import build_local_actor_urls, create_local_actor


@pytest.mark.django_db
def test_create_local_actor_generates_urls(settings):
    actor = create_local_actor("alice", name="Alice")
    base = settings.INSTANCE_URL
    assert actor.ap_id == f"{base}/actors/alice"
    assert actor.inbox == f"{base}/actors/alice/inbox"
    assert actor.outbox == f"{base}/actors/alice/outbox"
    assert actor.followers == f"{base}/actors/alice/followers"
    assert actor.shared_inbox == f"{base}/inbox"
    assert actor.is_local is True
    assert actor.name == "Alice"


@pytest.mark.django_db
def test_create_local_actor_keys_match_and_are_encrypted():
    actor = create_local_actor("alice")
    private_pem = crypto.decrypt(bytes(actor.private_key_pem))
    assert b"PRIVATE KEY" in private_pem
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    public_key = serialization.load_pem_public_key(actor.public_key_pem.encode())
    assert private_key.public_key().public_numbers() == public_key.public_numbers()

    ed_private = crypto.decrypt(bytes(actor.ed25519_private_key))
    derived = (
        ed25519.Ed25519PrivateKey.from_private_bytes(ed_private)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    assert base64.b64encode(derived).decode() == actor.ed25519_public_key


@pytest.mark.django_db
def test_create_local_actor_can_attach_user():
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(email="a@example.com", password="x")
    actor = create_local_actor("a", user=user)
    assert actor.user == user


def test_build_local_actor_urls_are_derived_from_instance_url(settings):
    settings.INSTANCE_URL = "https://example.org"
    urls = build_local_actor_urls("comic")
    assert urls["ap_id"] == "https://example.org/actors/comic"
    assert urls["shared_inbox"] == "https://example.org/inbox"
