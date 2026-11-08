from uuid import uuid4

import pytest
from django.urls import reverse

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from federation import follower_sync
from federation.delivery import fan_out
from federation.models import Delivery
from federation.remotes import fetch_remote_actor
from social.models import Follow, FollowStatus
from tests.federation_support import http_headers, signed_headers
from tests.test_interop import _peer_document


def _remote(handle="fan", domain="remote.test"):
    return Actor.objects.create(
        ap_id=f"https://{domain}/users/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/users/{handle}/inbox",
        shared_inbox=f"https://{domain}/inbox",
        followers=f"https://{domain}/users/{handle}/followers",
    )


def _local(handle="local"):
    return create_local_actor(handle)


@pytest.mark.django_db
def test_partial_digest_and_header_params():
    local = _local()
    remote = _remote()
    Follow.objects.create(follower=remote, target=local, status=FollowStatus.ACCEPTED)
    params = follower_sync.header_params(local, remote.inbox)
    assert params["collectionId"] == local.followers
    assert params["url"].endswith(f"/actors/{local.handle}/followers_synchronization")
    assert len(params["digest"]) == 64
    assert follower_sync.header_params(local, "https://other.test/inbox") == {}
    assert follower_sync.local_partial_digest(local) == "0" * 64
    assert follower_sync.parse_header(follower_sync.header_value(params)) == params


@pytest.mark.django_db
def test_synchronization_endpoint_requires_signature_and_returns_partial(
    client, monkeypatch
):
    local = _local("target")
    fixture = {
        "actor": {
            "id": "https://remote.test/users/fan",
            "preferredUsername": "fan",
            "name": "Fan",
            "inbox": "https://remote.test/users/fan/inbox",
            "endpoints": {"sharedInbox": "https://remote.test/inbox"},
        }
    }
    keyholder = create_local_actor("keyholder")
    document = _peer_document(fixture, keyholder)
    monkeypatch.setattr(
        "federation.remotes.client.fetch_json", lambda url, **kw: document
    )
    remote = fetch_remote_actor("https://remote.test/users/fan")
    Follow.objects.create(follower=remote, target=local, status=FollowStatus.ACCEPTED)
    path = reverse("actor-followers-synchronization", args=[local.handle])
    url = f"http://testserver{path}"
    assert client.get(path).status_code == 401
    headers = signed_headers(
        keyholder,
        url,
        b"",
        key_id=f"{remote.ap_id}#main-key",
        method="GET",
    )
    response = client.get(path, **http_headers(headers))
    assert response.status_code == 200
    assert response.json()["orderedItems"] == [remote.ap_id]


@pytest.mark.django_db
def test_reconcile_removes_unlisted_and_accepts_pending():
    remote = _remote()
    keep = create_local_actor(f"keep{uuid4().hex[:6]}")
    drop = create_local_actor(f"drop{uuid4().hex[:6]}")
    pending = create_local_actor(f"pend{uuid4().hex[:6]}")
    Follow.objects.create(follower=keep, target=remote, status=FollowStatus.ACCEPTED)
    Follow.objects.create(follower=drop, target=remote, status=FollowStatus.ACCEPTED)
    Follow.objects.create(follower=pending, target=remote, status=FollowStatus.PENDING)
    follower_sync.reconcile(remote, {keep.ap_id, pending.ap_id})
    assert Follow.objects.filter(follower=keep).exists()
    assert not Follow.objects.filter(follower=drop).exists()
    assert Follow.objects.get(follower=pending).status == FollowStatus.ACCEPTED


@pytest.mark.django_db
def test_receive_fetches_and_reconciles_on_digest_mismatch(monkeypatch):
    remote = _remote()
    local = create_local_actor(f"local{uuid4().hex[:6]}")
    Follow.objects.create(follower=local, target=remote, status=FollowStatus.ACCEPTED)
    header = follower_sync.header_value(
        {
            "collectionId": remote.followers,
            "url": "https://remote.test/users/fan/followers_synchronization",
            "digest": "0" * 64,
        }
    )
    monkeypatch.setattr(
        "federation.client.fetch_json",
        lambda url, **kw: {"orderedItems": []},
    )
    assert follower_sync.receive(remote, header) is True
    assert not Follow.objects.filter(follower=local, target=remote).exists()


@pytest.mark.django_db
def test_fan_out_stores_synchronization_header():
    local = _local()
    remote = _remote()
    Follow.objects.create(follower=remote, target=local, status=FollowStatus.ACCEPTED)
    fan_out({"type": "Create"}, [remote.inbox], local)
    delivery = Delivery.objects.get()
    assert delivery.synchronization["collectionId"] == local.followers
    assert delivery.synchronization["digest"]
