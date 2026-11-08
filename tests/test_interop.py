import json
from pathlib import Path

import pytest
import responses
from cryptography.hazmat.primitives import serialization
from django.urls import reverse

from actors.services import create_local_actor
from federation.client import signed_request
from federation.keys import ed25519_multikey, load_actor_keys
from federation.models import Activity, PeerSignaturePreference
from federation.remotes import fetch_remote_actor
from moderation.models import Report
from social.models import Boost, Comment, Follow, Like
from tests.federation_support import http_headers, post_activity, signed_headers
from tests.test_reader import _publish, _scene

FIXTURES = Path(__file__).resolve().parent / "interop" / "fixtures"
PEERS = ["mastodon", "gotosocial", "mitra"]


def _fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _peer_document(fixture, keyholder):
    keys = load_actor_keys(keyholder)
    public_pem = keys.rsa_public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    ed_raw = keys.ed25519_public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    document = dict(fixture["actor"])
    document["type"] = "Person"
    document["publicKey"] = {"publicKeyPem": public_pem}
    document["assertionMethod"] = {"publicKeyMultibase": ed25519_multikey(ed_raw)}
    return document


def _setup_peer(fixture, monkeypatch):
    keyholder = create_local_actor(f"keyholder{fixture['name'][:3]}{id(fixture)}"[:32])
    document = _peer_document(fixture, keyholder)
    monkeypatch.setattr(
        "federation.remotes.client.fetch_json", lambda url, **kw: document
    )
    remote = fetch_remote_actor(fixture["actor"]["id"])
    return keyholder, remote


def _render(value, mapping):
    if isinstance(value, dict):
        return {key: _render(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, mapping) for item in value]
    if isinstance(value, str):
        for token, replacement in mapping.items():
            value = value.replace(token, replacement)
    return value


@pytest.mark.django_db
@pytest.mark.parametrize("peer", PEERS)
def test_peer_activity_fixtures_are_accepted(client, monkeypatch, peer):
    fixture = _fixture(peer)
    keyholder, remote = _setup_peer(fixture, monkeypatch)
    _, comic, _, _, page, _, _ = _scene()
    _publish(page)
    page.refresh_from_db()
    mapping = {
        "__ACTOR__": remote.ap_id,
        "__LOCAL_ACTOR__": comic.actor.ap_id,
        "__PAGE__": page.ap_id,
    }
    for entry in fixture["activities"]:
        activity = _render(entry["payload"], mapping)
        response = post_activity(
            client,
            reverse("shared-inbox"),
            activity,
            keyholder,
            key_id=f"{remote.ap_id}#main-key",
            scheme=fixture["scheme"],
        )
        assert response.status_code == 202, entry["type"]
    assert Activity.objects.count() == len(fixture["activities"])
    assert Follow.objects.filter(follower=remote, target=comic.actor).exists()
    assert Comment.objects.filter(actor=remote).exists()
    assert Like.objects.filter(actor=remote).exists()
    assert Boost.objects.filter(actor=remote).exists()
    assert Report.objects.filter(reporter_actor=remote).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("scheme", ["cavage", "rfc9421"])
def test_inbound_accepts_both_signature_schemes(client, monkeypatch, scheme):
    fixture = _fixture("gotosocial")
    keyholder, remote = _setup_peer(fixture, monkeypatch)
    _, comic, _, _, _, _, _ = _scene()
    activity = _render(
        fixture["activities"][0]["payload"],
        {"__ACTOR__": remote.ap_id, "__LOCAL_ACTOR__": comic.actor.ap_id},
    )
    response = post_activity(
        client,
        reverse("shared-inbox"),
        activity,
        keyholder,
        key_id=f"{remote.ap_id}#main-key",
        scheme=scheme,
    )
    assert response.status_code == 202


@pytest.mark.django_db
def test_tampered_inbound_body_is_rejected(client, monkeypatch):
    fixture = _fixture("mastodon")
    keyholder, remote = _setup_peer(fixture, monkeypatch)
    _, comic, _, _, _, _, _ = _scene()
    activity = _render(
        fixture["activities"][0]["payload"],
        {"__ACTOR__": remote.ap_id, "__LOCAL_ACTOR__": comic.actor.ap_id},
    )
    body = json.dumps(activity).encode("utf-8")
    url = f"http://testserver{reverse('shared-inbox')}"
    headers = signed_headers(keyholder, url, body, key_id=f"{remote.ap_id}#main-key")
    tampered = body + b" "
    response = client.post(
        reverse("shared-inbox"),
        data=tampered,
        content_type="application/activity+json",
        **http_headers(headers),
    )
    assert response.status_code == 401


@responses.activate
@pytest.mark.django_db
def test_outbound_signed_request_prefers_cavage_and_sets_headers():
    actor = create_local_actor("outbound")
    url = "https://peer.test/inbox"
    responses.add(responses.POST, url, status=202)
    signed_request("POST", url, body={"type": "Create"}, actor=actor)
    request = responses.calls[0].request
    assert request.headers["Signature"]
    assert request.headers["Digest"]
    assert request.headers["Content-Digest"]
    assert request.headers["Content-Type"] == "application/activity+json"
    preference = PeerSignaturePreference.objects.get(domain="peer.test")
    assert preference.scheme == "cavage"


@responses.activate
@pytest.mark.django_db
def test_outbound_falls_back_to_rfc9421_on_401():
    actor = create_local_actor("fallback")
    url = "https://fallback.test/inbox"
    responses.add(responses.POST, url, status=401)
    responses.add(responses.POST, url, status=202)
    signed_request("POST", url, body={"type": "Create"}, actor=actor)
    assert len(responses.calls) == 2
    assert responses.calls[0].request.headers["Signature"]
    assert responses.calls[1].request.headers["Signature-Input"]
    assert (
        PeerSignaturePreference.objects.get(domain="fallback.test").scheme == "rfc9421"
    )


@pytest.mark.django_db
def test_declarations_list_signatures_and_features(client):
    federation_md = client.get(reverse("federation-md"))
    assert federation_md.status_code == 200
    text = federation_md.content.decode()
    assert "cavage" in text.lower()
    assert "RFC 9421" in text
    assert "delivery" in text.lower()
    assert "FEP-521a" in text
    nodeinfo = client.get(reverse("nodeinfo"))
    assert nodeinfo.status_code == 200
    assert "activitypub" in nodeinfo.json()["protocols"]
    discovery = client.get(reverse("nodeinfo-discovery"))
    assert discovery.json()["links"]


@responses.activate
def test_live_smoke_helpers_parse_nodeinfo_and_peers():
    from scripts import interop_smoke

    responses.add(
        responses.GET,
        "https://panels.test/.well-known/nodeinfo",
        json={"links": [{"href": "https://panels.test/nodeinfo/2.1"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://panels.test/nodeinfo/2.1",
        json={"protocols": ["activitypub"], "software": {"name": "panels"}},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://panels.test/FEDERATION.md",
        body="ActivityPub federation",
        status=200,
    )
    instance = interop_smoke.check_instance("https://panels.test")
    assert instance["nodeinfo"]["protocols"] == ["activitypub"]

    responses.add(
        responses.GET,
        "https://peer.test/users/fan",
        json={
            "id": "https://peer.test/users/fan",
            "type": "Person",
            "inbox": "https://peer.test/users/fan/inbox",
        },
        status=200,
    )
    actor = interop_smoke.check_peer("https://peer.test/users/fan")
    assert actor["id"] == "https://peer.test/users/fan"
