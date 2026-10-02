import pytest
from django.urls import reverse

from actors.services import create_local_actor
from federation.activitypub import PUBLIC
from federation.models import Activity, ActivityDirection, ActivityStatus


def _outbound_activity(actor, index):
    return Activity.objects.create(
        ap_id=f"https://panels.test/activities/{index}",
        type="Create",
        actor=actor,
        direction=ActivityDirection.OUTBOUND,
        status=ActivityStatus.PROCESSED,
        payload={
            "id": f"https://panels.test/activities/{index}",
            "type": "Create",
            "actor": actor.ap_id,
            "to": [PUBLIC],
        },
    )


@pytest.mark.django_db
def test_actor_outbox_paginates_public_outbound_activities(client):
    actor = create_local_actor("alice")
    for index in range(3):
        _outbound_activity(actor, index)
    Activity.objects.create(
        ap_id="https://panels.test/activities/private",
        type="Create",
        actor=actor,
        direction=ActivityDirection.OUTBOUND,
        status=ActivityStatus.PROCESSED,
        payload={
            "id": "https://panels.test/activities/private",
            "type": "Create",
            "actor": actor.ap_id,
            "to": [actor.followers],
        },
    )

    collection = client.get(reverse("actor-outbox", kwargs={"handle": "alice"})).json()
    assert collection["type"] == "OrderedCollection"
    assert collection["totalItems"] == 3
    page = client.get(
        reverse("actor-outbox", kwargs={"handle": "alice"}), {"page": 1}
    ).json()
    assert page["type"] == "OrderedCollectionPage"
    ids = [item["id"] for item in page["orderedItems"]]
    assert "https://panels.test/activities/private" not in ids
    assert len(ids) == 3


@pytest.mark.django_db
def test_empty_collections(client):
    create_local_actor("alice")
    for name in ("actor-followers", "actor-following", "actor-featured"):
        data = client.get(reverse(name, kwargs={"handle": "alice"})).json()
        assert data["type"] == "OrderedCollection"
        assert data["totalItems"] == 0


@pytest.mark.django_db
def test_unknown_actor_collection_returns_404(client):
    assert (
        client.get(reverse("actor-outbox", kwargs={"handle": "ghost"})).status_code
        == 404
    )


@pytest.mark.django_db
def test_actor_outbox_pagination_links(client):
    actor = create_local_actor("alice")
    for index in range(25):
        _outbound_activity(actor, index)
    first = client.get(
        reverse("actor-outbox", kwargs={"handle": "alice"}), {"page": 1}
    ).json()
    assert "next" in first
    assert "prev" not in first
    assert len(first["orderedItems"]) == 20
    second = client.get(
        reverse("actor-outbox", kwargs={"handle": "alice"}), {"page": 2}
    ).json()
    assert "prev" in second
    assert "next" not in second
    assert len(second["orderedItems"]) == 5
