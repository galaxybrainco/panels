import pytest
from django.db import IntegrityError, transaction

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from social.models import Follow, FollowStatus
from social.services import follower_inboxes


def local(handle="alice"):
    return create_local_actor(handle)


def remote(handle="bob", **kwargs):
    domain = f"{handle}.test"
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=kwargs.pop("inbox", f"https://{domain}/actors/{handle}/inbox"),
        shared_inbox=kwargs.pop("shared_inbox", ""),
        **kwargs,
    )


@pytest.mark.django_db
def test_follow_defaults_to_pending():
    follow = Follow.objects.create(follower=local("alice"), target=remote("bob"))
    assert follow.status == FollowStatus.PENDING


@pytest.mark.django_db
def test_follow_is_unique_per_pair():
    alice, bob = local("alice"), remote("bob")
    Follow.objects.create(follower=alice, target=bob)
    with pytest.raises(IntegrityError), transaction.atomic():
        Follow.objects.create(follower=alice, target=bob)


@pytest.mark.django_db
def test_self_follow_is_rejected():
    alice = local("alice")
    with pytest.raises(IntegrityError), transaction.atomic():
        Follow.objects.create(follower=alice, target=alice)


@pytest.mark.django_db
def test_follower_inboxes_dedupes_and_excludes_local_and_pending():
    target = local("comic")
    shared = remote("one", shared_inbox="https://one.test/inbox")
    also_shared = remote("two", shared_inbox="https://one.test/inbox")
    personal = remote("three")
    local_follower = local("fan")
    pending = remote("four")
    for follower in (shared, also_shared, personal):
        Follow.objects.create(
            follower=follower, target=target, status=FollowStatus.ACCEPTED
        )
    Follow.objects.create(
        follower=local_follower, target=target, status=FollowStatus.ACCEPTED
    )
    Follow.objects.create(follower=pending, target=target, status=FollowStatus.PENDING)

    inboxes = follower_inboxes(target)
    assert sorted(inboxes) == sorted(
        ["https://one.test/inbox", "https://one.test/inbox", personal.inbox]
    )
