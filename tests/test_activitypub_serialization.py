import pytest

from actors.services import create_local_actor
from federation.activitypub import (
    ACTIVITYSTREAMS_CONTEXT,
    PUBLIC,
    Audience,
    activity_context,
    addressing_for,
    build_activity,
    is_public,
    ordered_collection,
    ordered_collection_page,
)


def test_activity_context_is_mastodon_legible():
    assert activity_context() == [
        "https://www.w3.org/ns/activitystreams",
        "https://w3id.org/security/v1",
    ]


@pytest.mark.django_db
def test_build_activity_shape():
    actor = create_local_actor("alice")
    activity = build_activity(
        "Create",
        actor,
        {"type": "Note", "id": "https://panels.test/objects/1"},
        activity_id="https://panels.test/activities/1",
        to=[PUBLIC],
        cc=[actor.followers],
    )
    assert activity["type"] == "Create"
    assert activity["actor"] == actor.ap_id
    assert activity["id"] == "https://panels.test/activities/1"
    assert activity["to"] == [PUBLIC]
    assert activity["cc"] == [actor.followers]
    assert activity["@context"] == activity_context()


def test_addressing_for_audiences():
    followers = "https://panels.test/actors/alice/followers"
    assert addressing_for(Audience.PUBLIC, followers) == ([PUBLIC], [followers])
    assert addressing_for(Audience.UNLISTED, followers) == ([followers], [PUBLIC])
    assert addressing_for(Audience.FOLLOWERS_ONLY, followers) == ([followers], [])
    assert addressing_for(Audience.MEMBERS, followers) == ([], [])


def test_is_public_reads_to_and_cc():
    assert is_public({"to": [PUBLIC], "cc": []}) is True
    assert is_public({"to": ["x"], "cc": [PUBLIC]}) is True
    assert is_public({"to": ["x"], "cc": ["y"]}) is False
    assert is_public({}) is False


def test_is_public_accepts_scalar_and_object_addressing():
    assert is_public({"to": PUBLIC}) is True
    assert is_public({"object": {"to": [PUBLIC]}}) is True
    assert is_public({"object": {"cc": [PUBLIC]}}) is True
    assert is_public({"object": {"to": ["x"], "cc": ["y"]}}) is False


def test_collection_builders():
    collection = ordered_collection(
        "https://panels.test/actors/alice/outbox", total_items=2, first="p1"
    )
    assert collection["type"] == "OrderedCollection"
    assert collection["totalItems"] == 2
    assert collection["first"] == "p1"
    page = ordered_collection_page(
        "p1", "https://panels.test/actors/alice/outbox", ["a"], next_url="p2"
    )
    assert page["type"] == "OrderedCollectionPage"
    assert page["partOf"].endswith("/outbox")
    assert page["orderedItems"] == ["a"]
    assert page["next"] == "p2"


def test_activitystreams_context_constant():
    assert ACTIVITYSTREAMS_CONTEXT == "https://www.w3.org/ns/activitystreams"
