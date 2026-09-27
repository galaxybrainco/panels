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
