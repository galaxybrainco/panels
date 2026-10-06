import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.federation import emit_page_activity
from federation import collections, handlers
from federation.models import Activity, ActivityDirection, Delivery
from social.models import Follow, FollowStatus
from social.services import accept_follow, follow, follower_inboxes, unfollow


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


@pytest.mark.django_db
def test_follow_local_target_is_accepted_without_delivery():
    alice, comic = local("alice"), local("comic")
    result = follow(alice, comic)
    assert result.status == FollowStatus.ACCEPTED
    assert Delivery.objects.count() == 0


@pytest.mark.django_db
def test_follow_remote_target_is_pending_and_delivers_follow():
    alice, bob = local("alice"), remote("bob")
    result = follow(alice, bob)
    assert result.status == FollowStatus.PENDING
    delivery = Delivery.objects.get()
    assert delivery.inbox_url == bob.inbox
    assert delivery.activity["type"] == "Follow"
    assert delivery.activity["object"] == bob.ap_id


@pytest.mark.django_db
def test_follow_is_idempotent():
    alice, bob = local("alice"), remote("bob")
    follow(alice, bob)
    follow(alice, bob)
    assert Follow.objects.count() == 1
    assert Delivery.objects.count() == 1


@pytest.mark.django_db
def test_unfollow_remote_delivers_undo():
    alice, bob = local("alice"), remote("bob")
    follow(alice, bob)
    unfollow(alice, bob)
    assert not Follow.objects.exists()
    assert Delivery.objects.filter(activity__type="Undo").count() == 1


@pytest.mark.django_db
def test_accept_follow_marks_accepted_and_delivers_accept():
    bob, comic = remote("bob"), local("comic")
    incoming = Follow.objects.create(
        follower=bob, target=comic, status=FollowStatus.PENDING
    )
    accept_follow(incoming)
    incoming.refresh_from_db()
    assert incoming.status == FollowStatus.ACCEPTED
    assert Delivery.objects.filter(
        activity__type="Accept", inbox_url=bob.inbox
    ).exists()


def _inbound(activity_type, actor, payload):
    return Activity.objects.create(
        ap_id=payload["id"],
        type=activity_type,
        actor=actor,
        direction=ActivityDirection.INBOUND,
        payload=payload,
    )


@pytest.mark.django_db
def test_inbound_follow_auto_accepts_and_delivers_accept():
    comic = local("comic")
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Follow",
        "actor": bob.ap_id,
        "object": comic.ap_id,
    }
    handlers.dispatch(_inbound("Follow", bob, payload))
    follow_obj = Follow.objects.get(follower=bob, target=comic)
    assert follow_obj.status == FollowStatus.ACCEPTED
    assert Delivery.objects.filter(
        activity__type="Accept", inbox_url=bob.inbox
    ).exists()


@pytest.mark.django_db
def test_inbound_follow_to_manual_target_stays_pending():
    comic = local("comic")
    comic.manually_approves_followers = True
    comic.save()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Follow",
        "actor": bob.ap_id,
        "object": comic.ap_id,
    }
    handlers.dispatch(_inbound("Follow", bob, payload))
    assert Follow.objects.get(follower=bob, target=comic).status == FollowStatus.PENDING
    assert not Delivery.objects.filter(activity__type="Accept").exists()


@pytest.mark.django_db
def test_inbound_duplicate_follow_does_not_double_accept():
    comic, bob = local("comic"), remote("bob")
    first = {
        "id": "https://bob.test/activities/1",
        "type": "Follow",
        "actor": bob.ap_id,
        "object": comic.ap_id,
    }
    second = {**first, "id": "https://bob.test/activities/1b"}
    handlers.dispatch(_inbound("Follow", bob, first))
    handlers.dispatch(_inbound("Follow", bob, second))
    assert Follow.objects.count() == 1
    assert Delivery.objects.filter(activity__type="Accept").count() == 1


@pytest.mark.django_db
def test_inbound_undo_removes_follow():
    comic, bob = local("comic"), remote("bob")
    Follow.objects.create(follower=bob, target=comic, status=FollowStatus.ACCEPTED)
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": {"type": "Follow", "actor": bob.ap_id, "object": comic.ap_id},
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Follow.objects.exists()


@pytest.mark.django_db
def test_inbound_accept_accepts_our_pending_follow():
    alice, bob = local("alice"), remote("bob")
    follow(alice, bob)  # pending
    payload = {
        "id": "https://bob.test/activities/3",
        "type": "Accept",
        "actor": bob.ap_id,
        "object": {"type": "Follow", "actor": alice.ap_id, "object": bob.ap_id},
    }
    handlers.dispatch(_inbound("Accept", bob, payload))
    assert (
        Follow.objects.get(follower=alice, target=bob).status == FollowStatus.ACCEPTED
    )


@pytest.mark.django_db
def test_collections_list_accepted_follows():
    alice, bob = local("alice"), remote("bob")
    Follow.objects.create(follower=alice, target=bob, status=FollowStatus.ACCEPTED)
    pending = remote("carol")
    Follow.objects.create(follower=pending, target=bob, status=FollowStatus.PENDING)
    followers = collections.actor_followers(bob, page=1)
    assert followers["orderedItems"] == [alice.ap_id]
    following = collections.actor_following(alice, page=1)
    assert following["orderedItems"] == [bob.ap_id]


@pytest.mark.django_db
def test_publish_fans_out_to_accepted_remote_followers():
    from django.contrib.auth import get_user_model

    from comics.services import create_comic, create_page, create_series
    from tests.media_support import make_media

    owner = get_user_model().objects.create_user(email="o@example.com", password="x")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    page.ap_id = f"http://testserver/pages/{page.id}"
    bob = remote("bob", shared_inbox="https://bob.test/inbox")
    Follow.objects.create(
        follower=bob, target=comic.actor, status=FollowStatus.ACCEPTED
    )

    emit_page_activity(page, "Create")

    assert Delivery.objects.filter(
        inbox_url="https://bob.test/inbox", activity__type="Create"
    ).exists()


@pytest.mark.django_db
def test_self_follow_service_rejects():
    alice = local("alice")
    with pytest.raises(ValidationError):
        follow(alice, alice)


@pytest.mark.django_db
def test_follow_stores_activity_id_and_accept_by_string_references_it():
    alice, bob = local("alice"), remote("bob")
    follow_obj = follow(alice, bob)
    assert follow_obj.activity_id
    delivered = Delivery.objects.get(activity__type="Follow").activity
    assert delivered["id"] == follow_obj.activity_id

    payload = {
        "id": "https://bob.test/activities/9",
        "type": "Accept",
        "actor": bob.ap_id,
        "object": follow_obj.activity_id,
    }
    handlers.dispatch(_inbound("Accept", bob, payload))
    assert Follow.objects.get(pk=follow_obj.pk).status == FollowStatus.ACCEPTED


@pytest.mark.django_db
def test_accept_by_string_only_accepts_the_matching_follow():
    alice, carol, bob = local("alice"), local("carol"), remote("bob")
    alice_follow = follow(alice, bob)
    carol_follow = follow(carol, bob)
    payload = {
        "id": "https://bob.test/activities/9",
        "type": "Accept",
        "actor": bob.ap_id,
        "object": alice_follow.activity_id,
    }
    handlers.dispatch(_inbound("Accept", bob, payload))
    assert Follow.objects.get(pk=alice_follow.pk).status == FollowStatus.ACCEPTED
    assert Follow.objects.get(pk=carol_follow.pk).status == FollowStatus.PENDING


@pytest.mark.django_db
def test_accept_for_a_different_target_is_ignored():
    alice, bob, dave = local("alice"), remote("bob"), remote("dave")
    follow_obj = follow(alice, bob)
    payload = {
        "id": "https://bob.test/activities/9",
        "type": "Accept",
        "actor": bob.ap_id,
        "object": {"type": "Follow", "actor": alice.ap_id, "object": dave.ap_id},
    }
    handlers.dispatch(_inbound("Accept", bob, payload))
    assert Follow.objects.get(pk=follow_obj.pk).status == FollowStatus.PENDING


@pytest.mark.django_db
def test_inbound_follow_stores_original_id_and_accept_references_it():
    comic, bob = local("comic"), remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Follow",
        "actor": bob.ap_id,
        "object": comic.ap_id,
    }
    handlers.dispatch(_inbound("Follow", bob, payload))
    follow_obj = Follow.objects.get(follower=bob, target=comic)
    assert follow_obj.activity_id == "https://bob.test/activities/1"
    accept = Delivery.objects.get(activity__type="Accept").activity
    assert accept["object"]["id"] == "https://bob.test/activities/1"


@pytest.mark.django_db
def test_inbound_undo_with_string_object_removes_follow():
    comic, bob = local("comic"), remote("bob")
    Follow.objects.create(
        follower=bob,
        target=comic,
        status=FollowStatus.ACCEPTED,
        activity_id="https://bob.test/activities/1",
    )
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": "https://bob.test/activities/1",
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Follow.objects.exists()


@pytest.mark.django_db
def test_unfollow_undo_references_original_follow_id():
    alice, bob = local("alice"), remote("bob")
    follow_obj = follow(alice, bob)
    unfollow(alice, bob)
    undo = Delivery.objects.get(activity__type="Undo").activity
    assert undo["object"]["id"] == follow_obj.activity_id


@pytest.mark.django_db
def test_fan_out_dedupes_shared_inbox():
    from django.contrib.auth import get_user_model

    from comics.services import create_comic, create_page, create_series
    from tests.media_support import make_media

    owner = get_user_model().objects.create_user(email="o@example.com", password="x")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    page.ap_id = f"http://testserver/pages/{page.id}"
    one = remote("one", shared_inbox="https://shared.test/inbox")
    two = remote("two", shared_inbox="https://shared.test/inbox")
    for follower in (one, two):
        Follow.objects.create(
            follower=follower, target=comic.actor, status=FollowStatus.ACCEPTED
        )

    emit_page_activity(page, "Create")

    assert (
        Delivery.objects.filter(
            inbox_url="https://shared.test/inbox", activity__type="Create"
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_gated_page_does_not_fan_out():
    from django.contrib.auth import get_user_model

    from comics.services import create_comic, create_page, create_series
    from federation.activitypub import Audience
    from tests.media_support import make_media

    owner = get_user_model().objects.create_user(email="o@example.com", password="x")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series, audience=Audience.MEMBERS)
    make_media(page, position=1, alt_text="A panel")
    page.ap_id = f"http://testserver/pages/{page.id}"
    bob = remote("bob")
    Follow.objects.create(
        follower=bob, target=comic.actor, status=FollowStatus.ACCEPTED
    )

    assert emit_page_activity(page, "Create") is None
    assert Delivery.objects.count() == 0
