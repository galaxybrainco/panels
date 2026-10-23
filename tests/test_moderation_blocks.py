from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from moderation import blocks
from moderation.models import Block
from social.models import Follow, FollowStatus


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
