import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.urls import reverse

from actors.activitypub import actor_to_activitypub
from actors.handlers import handle_move
from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from federation.remotes import fetch_remote_actor
from media.services import add_media
from social.models import Follow, FollowStatus
from tests.media_support import upload
from tests.test_reader import _user


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path


def _remote(handle="old", domain="remote.test", **kwargs):
    return Actor.objects.create(
        ap_id=f"https://{domain}/users/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/users/{handle}/inbox",
        **kwargs,
    )


@pytest.mark.django_db
def test_actor_document_exposes_aliases_and_moved_to():
    actor = create_local_actor("alias")
    actor.also_known_as = ["https://new.test/users/alias"]
    actor.moved_to = "https://new.test/users/alias"
    actor.save(update_fields=["also_known_as", "moved_to", "updated_at"])
    document = actor_to_activitypub(actor)
    assert document["alsoKnownAs"] == ["https://new.test/users/alias"]
    assert document["movedTo"] == "https://new.test/users/alias"


@pytest.mark.django_db
def test_move_endpoint_verifies_target_and_emits_move(client, monkeypatch):
    owner = _user()
    old = create_local_actor(f"old{uuid4().hex[:6]}", user=owner)
    target = create_local_actor(f"new{uuid4().hex[:6]}")
    target.also_known_as = [old.ap_id]
    target.save(update_fields=["also_known_as", "updated_at"])
    remote_follower = _remote(handle="fan")
    Follow.objects.create(
        follower=remote_follower, target=old, status=FollowStatus.ACCEPTED
    )
    calls = []
    monkeypatch.setattr(
        "actors.portability.fan_out",
        lambda payload, inboxes, actor: calls.append((payload, inboxes)),
    )
    client.force_login(owner)
    response = client.post(
        reverse("actor-move", args=[old.handle]),
        data=json.dumps({"target": target.ap_id}),
        content_type="application/json",
    )
    assert response.status_code == 200
    old.refresh_from_db()
    assert old.moved_to == target.ap_id
    assert target.ap_id in old.also_known_as
    payload, inboxes = calls[0]
    assert payload["type"] == "Move"
    assert payload["target"] == target.ap_id
    assert inboxes == [remote_follower.inbox]

    client.force_login(_user())
    denied = client.post(
        reverse("actor-move", args=[old.handle]),
        data=json.dumps({"target": target.ap_id}),
        content_type="application/json",
    )
    assert denied.status_code == 403


@pytest.mark.django_db
def test_move_endpoint_rejects_unverified_target(client):
    owner = _user()
    old = create_local_actor(f"old{uuid4().hex[:6]}", user=owner)
    target = create_local_actor(f"new{uuid4().hex[:6]}")
    client.force_login(owner)
    response = client.post(
        reverse("actor-move", args=[old.handle]),
        data=json.dumps({"target": target.ap_id}),
        content_type="application/json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_inbound_move_migrates_follows_and_collapses_duplicates():
    old = _remote(handle="old")
    new = _remote(handle="new")
    new.also_known_as = [old.ap_id]
    new.save(update_fields=["also_known_as", "updated_at"])
    local = create_local_actor(f"local{uuid4().hex[:6]}")
    other = create_local_actor(f"other{uuid4().hex[:6]}")
    Follow.objects.create(follower=local, target=old, status=FollowStatus.ACCEPTED)
    Follow.objects.create(follower=other, target=old, status=FollowStatus.ACCEPTED)
    Follow.objects.create(follower=other, target=new, status=FollowStatus.ACCEPTED)

    activity = SimpleNamespace(
        actor=old,
        payload={"type": "Move", "target": new.ap_id},
    )
    handle_move(activity)

    assert Follow.objects.filter(follower=local, target=new).exists()
    assert not Follow.objects.filter(target=old).exists()
    assert Follow.objects.filter(follower=other, target=new).count() == 1
    old.refresh_from_db()
    assert old.moved_to == new.ap_id


@pytest.mark.django_db
def test_fetch_remote_actor_parses_aliases(monkeypatch):
    document = {
        "id": "https://remote.test/users/mover",
        "type": "Person",
        "preferredUsername": "mover",
        "inbox": "https://remote.test/users/mover/inbox",
        "alsoKnownAs": ["https://old.test/users/mover"],
        "movedTo": "https://remote.test/users/mover",
    }
    monkeypatch.setattr(
        "federation.remotes.client.fetch_json", lambda url, **kw: document
    )
    actor = fetch_remote_actor("https://remote.test/users/mover")
    assert actor.also_known_as == ["https://old.test/users/mover"]
    assert actor.moved_to == "https://remote.test/users/mover"


@pytest.mark.django_db
def test_export_account_writes_json_and_media(tmp_path):
    owner = _user()
    create_local_actor(f"exporter{uuid4().hex[:6]}", user=owner)
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Export Comic")
    series = create_series(owner, comic, "Main")
    page = create_page(owner, series, title="Page")
    media = add_media(owner, page, upload(), alt_text="Panel")
    output = tmp_path / "export.json"
    media_dir = tmp_path / "media"
    call_command(
        "export_account",
        owner.email,
        output=str(output),
        media_dir=str(media_dir),
    )
    data = json.loads(output.read_text())
    assert data["user"]["email"] == owner.email
    assert data["comics"][0]["slug"] == comic.slug
    assert data["comics"][0]["series"][0]["pages"][0]["media"][0]["file"] == (
        media.original.name
    )
    assert (media_dir / media.original.name).exists()
