import pytest
import responses

from actors.services import create_local_actor
from federation.client import fetch_json, post_activity
from federation.models import PeerSignaturePreference, SignatureScheme

INBOX = "https://other.test/inbox"


@pytest.mark.django_db
@responses.activate
def test_post_activity_signs_and_delivers():
    actor = create_local_actor("alice")
    responses.add(responses.POST, INBOX, status=202)
    response = post_activity(INBOX, {"type": "Create"}, actor)
    assert response.status_code == 202
    request = responses.calls[0].request
    assert "Signature" in request.headers
    assert request.headers["Content-Type"] == "application/activity+json"


@pytest.mark.django_db
@responses.activate
def test_double_knock_falls_back_to_rfc9421_and_remembers():
    actor = create_local_actor("alice")
    responses.add(responses.POST, INBOX, status=401)
    responses.add(responses.POST, INBOX, status=202)

    response = post_activity(INBOX, {"type": "Create"}, actor)
    assert response.status_code == 202
    preference = PeerSignaturePreference.objects.get(domain="other.test")
    assert preference.scheme == SignatureScheme.RFC9421


@pytest.mark.django_db
@responses.activate
def test_post_activity_uses_remembered_preference_first():
    actor = create_local_actor("alice")
    PeerSignaturePreference.objects.create(
        domain="other.test", scheme=SignatureScheme.RFC9421
    )
    responses.add(responses.POST, INBOX, status=202)
    post_activity(INBOX, {"type": "Create"}, actor)
    request = responses.calls[0].request
    assert "Signature-Input" in request.headers


@pytest.mark.django_db
@responses.activate
def test_fetch_json_signs_with_instance_actor():
    create_local_actor("instance", is_instance_actor=True)
    responses.add(
        responses.GET,
        "https://other.test/actors/bob",
        json={"id": "https://other.test/actors/bob", "type": "Person"},
        status=200,
    )
    document = fetch_json("https://other.test/actors/bob")
    assert document["type"] == "Person"
    assert "Signature" in responses.calls[0].request.headers


@pytest.mark.django_db
@responses.activate
def test_fetch_json_returns_none_on_error():
    create_local_actor("instance", is_instance_actor=True)
    responses.add(responses.GET, "https://other.test/actors/bob", status=404)
    assert fetch_json("https://other.test/actors/bob") is None
