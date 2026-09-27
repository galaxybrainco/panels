import pytest
from django.conf import settings
from django.urls import reverse

from actors.services import create_local_actor


@pytest.mark.django_db
def test_webfinger_resolves_local_actor_end_to_end(client):
    actor = create_local_actor("alice")
    response = client.get(
        reverse("webfinger"),
        {"resource": f"acct:alice@{settings.INSTANCE_DOMAIN}"},
    )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/jrd+json")
    data = response.json()
    assert data["subject"] == f"acct:alice@{settings.INSTANCE_DOMAIN}"
    self_link = next(link for link in data["links"] if link["rel"] == "self")
    assert self_link["href"] == actor.ap_id

    actor_response = client.get(self_link["href"])
    assert actor_response.status_code == 200
    assert actor_response.json()["id"] == actor.ap_id


@pytest.mark.django_db
def test_webfinger_unknown_actor_returns_404(client):
    response = client.get(
        reverse("webfinger"), {"resource": f"acct:nobody@{settings.INSTANCE_DOMAIN}"}
    )
    assert response.status_code == 404


def test_webfinger_missing_resource_returns_404(client):
    assert client.get(reverse("webfinger")).status_code == 404


def test_webfinger_malformed_resource_returns_404(client):
    response = client.get(reverse("webfinger"), {"resource": "not-an-acct"})
    assert response.status_code == 404


@pytest.mark.django_db
def test_webfinger_remote_domain_returns_404(client):
    create_local_actor("alice")
    response = client.get(reverse("webfinger"), {"resource": "acct:alice@other.test"})
    assert response.status_code == 404
