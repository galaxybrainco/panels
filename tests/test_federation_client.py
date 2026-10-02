import pytest
import requests
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
    instance = create_local_actor("instance", is_instance_actor=True)
    responses.add(
        responses.GET,
        "https://other.test/actors/bob",
        json={"id": "https://other.test/actors/bob", "type": "Person"},
        status=200,
    )
    document = fetch_json("https://other.test/actors/bob")
    assert document["type"] == "Person"
    signature = responses.calls[0].request.headers["Signature"]
    assert f"{instance.ap_id}#main-key" in signature


@pytest.mark.django_db
@responses.activate
def test_fetch_json_rejects_non_https():
    create_local_actor("instance", is_instance_actor=True)
    assert fetch_json("http://other.test/actors/bob") is None
    assert len(responses.calls) == 0


@pytest.mark.django_db
@responses.activate
def test_fetch_json_rejects_private_and_link_local_addresses():
    create_local_actor("instance", is_instance_actor=True)
    assert fetch_json("https://169.254.169.254/latest/meta-data/") is None
    assert fetch_json("https://127.0.0.1/actors/bob") is None
    assert fetch_json("https://10.0.0.1/actors/bob") is None
    assert len(responses.calls) == 0


@pytest.mark.django_db
@responses.activate
def test_fetch_json_rejects_unresolvable_host():
    create_local_actor("instance", is_instance_actor=True)
    assert fetch_json("https://does-not-resolve.invalid/actors/bob") is None
    assert len(responses.calls) == 0


@pytest.mark.django_db
@responses.activate
def test_fetch_json_rejects_hostname_resolving_to_private_ip(monkeypatch):
    import socket

    create_local_actor("instance", is_instance_actor=True)
    monkeypatch.setattr(
        "federation.client.getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))
        ],
    )
    assert fetch_json("https://evil.test/actors/bob") is None
    assert len(responses.calls) == 0


@pytest.mark.django_db
@responses.activate
def test_fetch_json_does_not_follow_redirects():
    create_local_actor("instance", is_instance_actor=True)
    responses.add(
        responses.GET,
        "https://other.test/actors/bob",
        status=302,
        headers={"Location": "https://evil.test/actors/bob"},
    )
    assert fetch_json("https://other.test/actors/bob") is None
    assert len(responses.calls) == 1


@pytest.mark.django_db
@responses.activate
def test_fetch_json_returns_none_on_network_error():
    create_local_actor("instance", is_instance_actor=True)
    responses.add(
        responses.GET,
        "https://other.test/actors/bob",
        body=requests.exceptions.ConnectionError("boom"),
    )
    assert fetch_json("https://other.test/actors/bob") is None


@pytest.mark.django_db
@responses.activate
def test_fetch_json_returns_none_on_error():
    create_local_actor("instance", is_instance_actor=True)
    responses.add(responses.GET, "https://other.test/actors/bob", status=404)
    assert fetch_json("https://other.test/actors/bob") is None
