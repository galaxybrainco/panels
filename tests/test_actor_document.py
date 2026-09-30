import base64

import base58
import pytest
from django.urls import reverse

from actors.services import create_local_actor


@pytest.mark.django_db
def test_actor_document_exposes_public_fields_only(client):
    actor = create_local_actor("alice", name="Alice")
    response = client.get(reverse("actor-detail", kwargs={"handle": "alice"}))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/activity+json")
    data = response.json()
    assert data["id"] == actor.ap_id
    assert data["type"] == "Person"
    assert data["preferredUsername"] == "alice"
    assert data["name"] == "Alice"
    assert data["inbox"] == actor.inbox
    assert data["publicKey"]["publicKeyPem"] == actor.public_key_pem
    assert data["publicKey"]["owner"] == actor.ap_id
    assert data["endpoints"]["sharedInbox"] == actor.shared_inbox


@pytest.mark.django_db
def test_actor_document_does_not_leak_private_keys(client):
    create_local_actor("alice")
    response = client.get(reverse("actor-detail", kwargs={"handle": "alice"}))
    body = response.content.decode()
    data = response.json()
    assert {"private_key_pem", "ed25519_private_key", "privateKey"}.isdisjoint(data)
    assert "PRIVATE KEY" not in body
    assert "BEGIN PUBLIC KEY" in body


@pytest.mark.django_db
def test_unknown_actor_returns_404(client):
    response = client.get(reverse("actor-detail", kwargs={"handle": "ghost"}))
    assert response.status_code == 404


@pytest.mark.django_db
def test_actor_document_advertises_ed25519_assertion_method(client):
    from actors.services import create_local_actor

    actor = create_local_actor("alice")
    data = client.get(reverse("actor-detail", kwargs={"handle": "alice"})).json()
    method = data["assertionMethod"]
    assert method["id"] == f"{actor.ap_id}#ed25519-key"
    assert method["type"] == "Multikey"
    assert method["controller"] == actor.ap_id
    decoded = base58.b58decode(method["publicKeyMultibase"][1:])
    assert decoded[:2] == b"\xed\x01"
    assert decoded[2:] == base64.b64decode(actor.ed25519_public_key)
    assert data["publicKey"]["publicKeyPem"] == actor.public_key_pem
