import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse


def test_nodeinfo_discovery_points_at_schema_21(client):
    response = client.get(reverse("nodeinfo-discovery"))
    assert response.status_code == 200
    link = response.json()["links"][0]
    assert link["rel"] == "http://nodeinfo.diaspora.software/ns/schema/2.1"
    assert link["href"].endswith(reverse("nodeinfo"))


@pytest.mark.django_db
def test_nodeinfo_document_shape(client, settings):
    response = client.get(reverse("nodeinfo"))
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "2.1"
    assert data["software"]["name"] == "panels"
    assert data["protocols"] == ["activitypub"]
    assert data["openRegistrations"] is False
    assert data["metadata"]["nodeName"] == settings.INSTANCE_NAME


@pytest.mark.django_db
def test_nodeinfo_counts_users(client):
    get_user_model().objects.create_user(email="a@example.com", password="x")
    data = client.get(reverse("nodeinfo")).json()
    assert data["usage"]["users"]["total"] == 1


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=True)
def test_nodeinfo_reflects_open_registrations(client):
    assert client.get(reverse("nodeinfo")).json()["openRegistrations"] is True
