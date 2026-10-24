from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from federation.models import Activity
from federation.remotes import fetch_remote_actor
from moderation import blocks
from moderation.models import Block
from moderation.services import notification_allowed
from social.models import Follow, FollowStatus
from tests.federation_support import post_activity
from tests.test_federation_remotes import _remote_document


def _user():
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


def _personal(user):
    return create_local_actor(f"u{uuid4().hex[:8]}", user=user)


def _remote(handle="bob", domain="remote.test"):
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
        shared_inbox=f"https://{domain}/inbox",
    )


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        blocks,
        "fan_out",
        lambda payload, inboxes, actor: calls.append((payload, inboxes)),
    )
    return calls


@pytest.mark.django_db
def test_block_removes_follows_both_directions(sent):
    user = _user()
    me = _personal(user)
    target = _remote()
    Follow.objects.create(follower=me, target=target, status=FollowStatus.PENDING)
    Follow.objects.create(follower=target, target=me, status=FollowStatus.ACCEPTED)

    block = blocks.block_actor(user, target)

    assert block.blocker == me
    assert block.blocked == target
    assert not Follow.objects.filter(follower=me, target=target).exists()
    assert not Follow.objects.filter(follower=target, target=me).exists()


@pytest.mark.django_db
def test_block_remote_emits_one_block(sent):
    user = _user()
    me = _personal(user)
    target = _remote()

    block = blocks.block_actor(user, target)
    blocks.block_actor(user, target)

    assert len(sent) == 1
    payload, inboxes = sent[0]
    assert payload["type"] == "Block"
    assert payload["actor"] == me.ap_id
    assert payload["object"] == target.ap_id
    assert payload["id"].startswith(f"{me.ap_id}#blocks/")
    assert inboxes == ["https://remote.test/inbox"]
    assert block.activity_id == payload["id"]


@pytest.mark.django_db
def test_block_local_emits_nothing(sent):
    user = _user()
    _personal(user)
    other = _personal(_user())

    block = blocks.block_actor(user, other)

    assert sent == []
    assert block.activity_id == ""


@pytest.mark.django_db
def test_unblock_remote_emits_undo_and_deletes(sent):
    user = _user()
    _personal(user)
    target = _remote()
    block = blocks.block_actor(user, target)
    sent.clear()

    assert blocks.unblock_actor(user, target) is True

    assert not Block.objects.filter(pk=block.pk).exists()
    assert len(sent) == 1
    payload, _ = sent[0]
    assert payload["type"] == "Undo"
    assert payload["object"]["type"] == "Block"
    assert payload["object"]["id"] == block.activity_id
    assert payload["object"]["object"] == target.ap_id
    assert blocks.unblock_actor(user, target) is False


@pytest.mark.django_db
def test_block_requires_personal_actor_and_rejects_self(sent):
    user = _user()
    target = _remote()
    with pytest.raises(blocks.NoActorError):
        blocks.block_actor(user, target)
    me = _personal(user)
    with pytest.raises(blocks.SelfBlockError):
        blocks.block_actor(user, me)


@pytest.mark.django_db
def test_block_helpers_cover_both_directions(sent):
    user = _user()
    me = _personal(user)
    target = _remote()
    blocker = _remote(handle="blocker")

    assert blocks.has_block_with_local(target) is False
    blocks.block_actor(user, target)
    assert blocks.has_block_with_local(target) is True
    assert blocks.is_blocked(user, target) is True
    assert target.id in blocks.hidden_actor_ids(user)

    Block.objects.create(blocker=blocker, blocked=me)
    assert blocks.has_block_with_local(blocker) is True
    assert blocks.is_blocked(user, blocker) is True
    assert blocker.id in blocks.hidden_actor_ids(user)


@pytest.mark.django_db
def test_should_drop_inbound_allows_undo(sent):
    user = _user()
    _personal(user)
    target = _remote()
    blocks.block_actor(user, target)
    assert blocks.should_drop_inbound(target, "Create") is True
    assert blocks.should_drop_inbound(target, "Undo") is False
    assert blocks.should_drop_inbound(_remote(handle="other"), "Create") is False


def _activity(actor, payload):
    return Activity.objects.create(
        ap_id=payload["id"],
        type=payload.get("type", ""),
        actor=actor,
        payload=payload,
    )


@pytest.mark.django_db
def test_inbound_block_stores_and_removes_follows(sent):
    me = _personal(_user())
    remote = _remote()
    Follow.objects.create(follower=remote, target=me, status=FollowStatus.ACCEPTED)
    payload = {
        "id": "https://remote.test/blocks/1",
        "type": "Block",
        "actor": remote.ap_id,
        "object": me.ap_id,
    }
    activity = _activity(remote, payload)

    from moderation.handlers import handle_block

    handle_block(activity)

    block = Block.objects.get(blocker=remote, blocked=me)
    assert block.activity_id == payload["id"]
    assert not Follow.objects.filter(follower=remote, target=me).exists()


@pytest.mark.django_db
def test_inbound_undo_block_removes_row(sent):
    me = _personal(_user())
    remote = _remote()
    Block.objects.create(
        blocker=remote, blocked=me, activity_id="https://remote.test/blocks/1"
    )
    payload = {
        "id": "https://remote.test/undos/1",
        "type": "Undo",
        "actor": remote.ap_id,
        "object": {
            "id": "https://remote.test/blocks/1",
            "type": "Block",
            "actor": remote.ap_id,
            "object": me.ap_id,
        },
    }
    activity = _activity(remote, payload)

    from moderation.blocks import handle_undo_block

    handle_undo_block(activity, payload["object"])

    assert not Block.objects.filter(blocker=remote, blocked=me).exists()


@pytest.mark.django_db
def test_inbound_block_of_unknown_actor_is_ignored(sent):
    remote = _remote()
    payload = {
        "id": "https://remote.test/blocks/2",
        "type": "Block",
        "actor": remote.ap_id,
        "object": "https://elsewhere.test/actors/nobody",
    }
    from moderation.blocks import record_inbound_block

    assert record_inbound_block(_activity(remote, payload)) is None


@pytest.mark.django_db
def test_notification_gate_suppresses_blocks_both_directions(sent):
    user = _user()
    me = _personal(user)
    target = _remote()
    blocks.block_actor(user, target)
    assert notification_allowed(user, actor=target, page=None) is False

    blocker = _remote(handle="blocker")
    Block.objects.create(blocker=blocker, blocked=me)
    assert notification_allowed(user, actor=blocker, page=None) is False
    assert notification_allowed(user, actor=_remote(handle="fine"), page=None) is True


@pytest.mark.django_db
def test_block_endpoint_round_trip(client, sent):
    user = _user()
    _personal(user)
    target = _remote()
    client.force_login(user)

    response = client.post(f"/moderation/blocks/{target.id}")
    assert response.status_code == 200
    assert response.json() == {"blocked": True}
    assert Block.objects.filter(blocked=target).exists()

    response = client.delete(f"/moderation/blocks/{target.id}")
    assert response.status_code == 200
    assert response.json() == {"blocked": False}
    assert not Block.objects.filter(blocked=target).exists()


@pytest.mark.django_db
def test_block_endpoint_requires_personal_actor(client):
    user = _user()
    target = _remote()
    client.force_login(user)
    response = client.post(f"/moderation/blocks/{target.id}")
    assert response.status_code == 409
    assert response.json() == {"error": "no_actor"}


@pytest.mark.django_db
def test_block_endpoint_rejects_self_and_unknown(client):
    user = _user()
    me = _personal(user)
    client.force_login(user)
    assert client.post(f"/moderation/blocks/{me.id}").status_code == 400
    assert client.post(f"/moderation/blocks/{uuid4()}").status_code == 404


@pytest.mark.django_db
def test_block_endpoint_requires_login(client):
    target = _remote()
    assert client.post(f"/moderation/blocks/{target.id}").status_code == 302


REMOTE = "https://other.test/actors/bob"
REMOTE_KEY_ID = f"{REMOTE}#main-key"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def remote_actor(monkeypatch):
    monkeypatch.setattr(
        "federation.remotes.client.fetch_json", lambda url, **kw: _remote_document()
    )
    return fetch_remote_actor(REMOTE)


@pytest.fixture
def keyholder(remote_actor):
    return Actor.objects.get(handle="keyholder")


@pytest.mark.django_db
def test_late_undo_does_not_clear_a_newer_block(sent):
    me = _personal(_user())
    remote = _remote()
    first = {
        "id": "https://remote.test/blocks/1",
        "type": "Block",
        "actor": remote.ap_id,
        "object": me.ap_id,
    }
    blocks.record_inbound_block(_activity(remote, first))
    undo = _activity(remote, {"id": "https://remote.test/undos/1", "type": "Undo"})
    blocks.handle_undo_block(undo, first)
    second = {
        "id": "https://remote.test/blocks/2",
        "type": "Block",
        "actor": remote.ap_id,
        "object": me.ap_id,
    }
    blocks.record_inbound_block(_activity(remote, second))

    blocks.handle_undo_block(undo, first)

    row = Block.objects.get(blocker=remote, blocked=me)
    assert row.activity_id == second["id"]


@pytest.mark.django_db
def test_duplicate_block_updates_activity_id(sent):
    me = _personal(_user())
    remote = _remote()
    first = {
        "id": "https://remote.test/blocks/1",
        "type": "Block",
        "actor": remote.ap_id,
        "object": me.ap_id,
    }
    blocks.record_inbound_block(_activity(remote, first))
    second = {**first, "id": "https://remote.test/blocks/2"}
    blocks.record_inbound_block(_activity(remote, second))
    assert Block.objects.get(blocker=remote, blocked=me).activity_id == second["id"]


@pytest.mark.django_db
def test_should_drop_inbound_allows_reciprocal_block(sent):
    user = _user()
    _personal(user)
    target = _remote()
    blocks.block_actor(user, target)
    assert blocks.should_drop_inbound(target, "Block") is False


@pytest.mark.django_db
def test_string_undo_block_removes_row(remote_actor):
    me = _personal(_user())
    Block.objects.create(
        blocker=remote_actor, blocked=me, activity_id="https://remote.test/blocks/1"
    )
    payload = {
        "id": "https://remote.test/undos/1",
        "type": "Undo",
        "actor": remote_actor.ap_id,
        "object": "https://remote.test/blocks/1",
    }
    activity = _activity(remote_actor, payload)

    from social.handlers import handle_undo

    handle_undo(activity)

    assert not Block.objects.filter(blocker=remote_actor, blocked=me).exists()


@pytest.mark.django_db
def test_inbox_drops_blocked_actor_without_storing(client, keyholder, remote_actor):
    user = _user()
    _personal(user)
    blocks.block_actor(user, remote_actor)
    payload = {
        "id": "https://other.test/activities/1",
        "type": "Create",
        "actor": REMOTE,
        "object": {"id": "https://other.test/notes/1", "type": "Note"},
    }
    response = post_activity(
        client, reverse("shared-inbox"), payload, keyholder, key_id=REMOTE_KEY_ID
    )
    assert response.status_code == 202
    assert not Activity.objects.filter(ap_id=payload["id"]).exists()


@pytest.mark.django_db
def test_inbox_stores_reciprocal_block(client, keyholder, remote_actor):
    user = _user()
    me = _personal(user)
    blocks.block_actor(user, remote_actor)
    payload = {
        "id": "https://other.test/blocks/1",
        "type": "Block",
        "actor": REMOTE,
        "object": me.ap_id,
    }
    response = post_activity(
        client, reverse("shared-inbox"), payload, keyholder, key_id=REMOTE_KEY_ID
    )
    assert response.status_code == 202
    assert Block.objects.filter(blocker=remote_actor, blocked=me).exists()
