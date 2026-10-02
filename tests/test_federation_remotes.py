import pytest
from cryptography.hazmat.primitives import serialization

from actors.models import Actor, Instance
from actors.services import create_local_actor
from federation.keys import ed25519_multikey, load_actor_keys
from federation.remotes import fetch_remote_actor, resolve_actor_by_key_id

REMOTE = "https://other.test/actors/bob"


def _remote_document():
    actor = create_local_actor("keyholder")
    keys = load_actor_keys(actor)
    public_pem = keys.rsa_public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    ed_raw = keys.ed25519_public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "id": REMOTE,
        "type": "Person",
        "preferredUsername": "bob",
        "name": "Bob",
        "inbox": "https://other.test/actors/bob/inbox",
        "endpoints": {"sharedInbox": "https://other.test/inbox"},
        "publicKey": {"publicKeyPem": public_pem},
        "assertionMethod": {"publicKeyMultibase": ed25519_multikey(ed_raw)},
    }


@pytest.mark.django_db
def test_fetch_remote_actor_upserts_actor_and_instance(monkeypatch):
    monkeypatch.setattr(
        "federation.remotes.client.fetch_json", lambda url, **kw: _remote_document()
    )
    actor = fetch_remote_actor(REMOTE)
    assert actor is not None
    assert actor.ap_id == REMOTE
    assert actor.domain == "other.test"
    assert actor.handle == "bob"
    assert actor.shared_inbox == "https://other.test/inbox"
    assert actor.public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")
    assert actor.ed25519_public_key
    assert actor.last_fetched_at is not None
    assert Instance.objects.filter(domain="other.test").exists()
    assert Actor.objects.filter(ap_id=REMOTE).count() == 1


@pytest.mark.django_db
def test_fetch_remote_actor_returns_none_when_fetch_fails(monkeypatch):
    monkeypatch.setattr("federation.remotes.client.fetch_json", lambda url, **kw: None)
    assert fetch_remote_actor(REMOTE) is None


@pytest.mark.django_db
def test_resolve_actor_by_key_id_uses_local_actor():
    actor = create_local_actor("alice")
    assert resolve_actor_by_key_id(f"{actor.ap_id}#main-key") == actor


@pytest.mark.django_db
def test_resolve_actor_by_key_id_fetches_remote(monkeypatch):
    monkeypatch.setattr(
        "federation.remotes.client.fetch_json", lambda url, **kw: _remote_document()
    )
    actor = resolve_actor_by_key_id(f"{REMOTE}#main-key")
    assert actor is not None
    assert actor.ap_id == REMOTE


@pytest.mark.django_db
def test_resolve_actor_by_key_id_returns_none_when_fetch_fails(monkeypatch):
    monkeypatch.setattr("federation.remotes.client.fetch_json", lambda url, **kw: None)
    assert resolve_actor_by_key_id(f"{REMOTE}#main-key") is None
