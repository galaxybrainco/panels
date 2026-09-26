import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from actors.models import Actor


def test_local_actor_has_no_domain(db):
    actor = Actor.objects.create(
        ap_id="https://panels.test/actors/alice", handle="alice"
    )
    assert actor.is_local is True


def test_remote_actor_has_domain(db):
    actor = Actor.objects.create(
        ap_id="https://other.test/users/bob", handle="bob", domain="other.test"
    )
    assert actor.is_local is False


def test_handle_is_unique_per_domain(db):
    Actor.objects.create(ap_id="https://panels.test/actors/alice", handle="alice")
    with pytest.raises(IntegrityError), transaction.atomic():
        Actor.objects.create(
            ap_id="https://panels.test/actors/alice-two", handle="alice"
        )


def test_same_handle_allowed_across_domains(db):
    Actor.objects.create(ap_id="https://panels.test/actors/alice", handle="alice")
    remote = Actor.objects.create(
        ap_id="https://other.test/users/alice", handle="alice", domain="other.test"
    )
    assert remote.pk is not None


def test_ap_id_is_unique(db):
    Actor.objects.create(ap_id="https://panels.test/actors/alice", handle="alice")
    with pytest.raises(IntegrityError), transaction.atomic():
        Actor.objects.create(
            ap_id="https://panels.test/actors/alice", handle="alice-two"
        )


def test_actor_links_to_user_both_ways(db):
    user = get_user_model().objects.create_user(email="a@example.com", password="x")
    actor = Actor.objects.create(
        ap_id="https://panels.test/actors/a", handle="a", user=user
    )
    assert actor.user == user
    assert user.actor == actor
