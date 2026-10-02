import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from actors.models import Actor, Instance
from actors.services import create_local_actor
from federation.handlers import HANDLERS, register
from federation.models import Activity, ActivityStatus
from federation.remotes import fetch_remote_actor
from tests.federation_support import post_activity
from tests.test_federation_remotes import REMOTE, _remote_document

PUBLIC = "https://www.w3.org/ns/activitystreams#Public"
REMOTE_KEY_ID = f"{REMOTE}#main-key"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _clean_registry():
    original = dict(HANDLERS)
    yield
    HANDLERS.clear()
    HANDLERS.update(original)


@pytest.fixture
def remote_actor(monkeypatch):
    monkeypatch.setattr(
        "federation.remotes.client.fetch_json", lambda url, **kw: _remote_document()
    )
    return fetch_remote_actor(REMOTE)


@pytest.fixture
def keyholder(remote_actor):
    return Actor.objects.get(handle="keyholder")


def _post(client, path, activity, keyholder):
    return post_activity(client, path, activity, keyholder, key_id=REMOTE_KEY_ID)


@pytest.mark.django_db
def test_shared_inbox_stores_and_dispatches(client, remote_actor, keyholder):
    seen = []
    register("Create")(lambda activity: seen.append(activity.ap_id))
    activity = {
        "id": "https://other.test/activities/1",
        "type": "Create",
        "actor": REMOTE,
        "to": [PUBLIC],
    }
    response = _post(client, reverse("shared-inbox"), activity, keyholder)
    assert response.status_code == 202
    stored = Activity.objects.get(ap_id=activity["id"])
    assert stored.actor == remote_actor
    assert seen == [activity["id"]]


@pytest.mark.django_db
def test_inbox_is_idempotent(client, remote_actor, keyholder):
    activity = {
        "id": "https://other.test/activities/2",
        "type": "Like",
        "actor": REMOTE,
    }
    _post(client, reverse("shared-inbox"), activity, keyholder)
    _post(client, reverse("shared-inbox"), activity, keyholder)
    assert Activity.objects.filter(ap_id=activity["id"]).count() == 1


@pytest.mark.django_db
def test_inbox_rejects_unsigned(client):
    response = client.post(
        reverse("shared-inbox"),
        data=b'{"id":"x","type":"Create","actor":"y"}',
        content_type="application/activity+json",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_inbox_rejects_actor_mismatch(client, remote_actor, keyholder):
    activity = {
        "id": "https://other.test/activities/3",
        "type": "Create",
        "actor": "https://other.test/actors/eve",
    }
    response = _post(client, reverse("shared-inbox"), activity, keyholder)
    assert response.status_code == 403


@pytest.mark.django_db
def test_inbox_rejects_blocked_instance(client, remote_actor, keyholder):
    Instance.objects.filter(domain="other.test").update(blocked=True)
    activity = {
        "id": "https://other.test/activities/4",
        "type": "Like",
        "actor": REMOTE,
    }
    response = _post(client, reverse("shared-inbox"), activity, keyholder)
    assert response.status_code == 403
    assert not Activity.objects.filter(ap_id=activity["id"]).exists()


@pytest.mark.django_db
def test_inbox_strips_media_for_reject_media_instance(client, remote_actor, keyholder):
    Instance.objects.filter(domain="other.test").update(reject_media=True)
    activity = {
        "id": "https://other.test/activities/5",
        "type": "Create",
        "actor": REMOTE,
        "object": {
            "type": "Note",
            "attachment": [{"type": "Document", "url": "https://other.test/a.png"}],
        },
    }
    response = _post(client, reverse("shared-inbox"), activity, keyholder)
    assert response.status_code == 202
    stored = Activity.objects.get(ap_id=activity["id"])
    assert "attachment" not in stored.payload["object"]


@pytest.mark.django_db
def test_actor_inbox_routes_to_handle(client, remote_actor, keyholder):
    create_local_actor("alice")
    activity = {
        "id": "https://other.test/activities/6",
        "type": "Like",
        "actor": REMOTE,
    }
    response = _post(
        client,
        reverse("actor-inbox", kwargs={"handle": "alice"}),
        activity,
        keyholder,
    )
    assert response.status_code == 202


@pytest.mark.django_db
def test_inbox_rate_limited(client, remote_actor, keyholder, monkeypatch):
    monkeypatch.setattr("federation.inbound.INBOUND_LIMIT", 1)
    first_activity = {
        "id": "https://other.test/activities/7",
        "type": "Like",
        "actor": REMOTE,
    }
    second_activity = {
        "id": "https://other.test/activities/8",
        "type": "Like",
        "actor": REMOTE,
    }
    first = _post(client, reverse("shared-inbox"), first_activity, keyholder)
    second = _post(client, reverse("shared-inbox"), second_activity, keyholder)
    assert first.status_code == 202
    assert second.status_code == 429


@pytest.mark.django_db
def test_inbox_accepts_signed_request_with_csrf_enforced(remote_actor, keyholder):
    client = Client(enforce_csrf_checks=True)
    activity = {
        "id": "https://other.test/activities/9",
        "type": "Like",
        "actor": REMOTE,
    }
    response = _post(client, reverse("shared-inbox"), activity, keyholder)
    assert response.status_code == 202


@pytest.mark.django_db
def test_inbox_per_actor_rate_limited(client, remote_actor, keyholder, monkeypatch):
    monkeypatch.setattr("federation.inbound.INBOUND_ACTOR_LIMIT", 1)
    first_activity = {
        "id": "https://other.test/activities/10",
        "type": "Like",
        "actor": REMOTE,
    }
    second_activity = {
        "id": "https://other.test/activities/11",
        "type": "Like",
        "actor": REMOTE,
    }
    assert (
        _post(client, reverse("shared-inbox"), first_activity, keyholder).status_code
        == 202
    )
    assert (
        _post(client, reverse("shared-inbox"), second_activity, keyholder).status_code
        == 429
    )


@pytest.mark.django_db
def test_inbox_drops_flag_when_reports_rejected(client, remote_actor, keyholder):
    Instance.objects.filter(domain="other.test").update(reject_reports=True)
    activity = {
        "id": "https://other.test/activities/12",
        "type": "Flag",
        "actor": REMOTE,
    }
    response = _post(client, reverse("shared-inbox"), activity, keyholder)
    assert response.status_code == 202
    assert not Activity.objects.filter(ap_id=activity["id"]).exists()


@pytest.mark.django_db
def test_inbox_rejects_key_substitution(client, remote_actor, keyholder):
    other = create_local_actor("otherkey")
    activity = {
        "id": "https://other.test/activities/13",
        "type": "Like",
        "actor": other.ap_id,
    }
    response = post_activity(
        client,
        reverse("shared-inbox"),
        activity,
        keyholder,
        key_id=f"{other.ap_id}#main-key",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_inbox_does_not_redispatch_processed_activity(client, remote_actor, keyholder):
    seen = []
    register("Create")(lambda activity: seen.append(activity.ap_id))
    activity = {
        "id": "https://other.test/activities/14",
        "type": "Create",
        "actor": REMOTE,
    }
    _post(client, reverse("shared-inbox"), activity, keyholder)
    _post(client, reverse("shared-inbox"), activity, keyholder)
    assert seen == [activity["id"]]


@pytest.mark.django_db
def test_inbox_retries_rejected_activity(client, remote_actor, keyholder):
    def boom(activity):
        raise RuntimeError("handler failed")

    register("Create")(boom)
    activity = {
        "id": "https://other.test/activities/15",
        "type": "Create",
        "actor": REMOTE,
    }
    _post(client, reverse("shared-inbox"), activity, keyholder)
    stored = Activity.objects.get(ap_id=activity["id"])
    assert stored.status == ActivityStatus.REJECTED

    HANDLERS.pop("Create")
    register("Create")(lambda activity: None)
    _post(client, reverse("shared-inbox"), activity, keyholder)
    stored.refresh_from_db()
    assert stored.status == ActivityStatus.PROCESSED


@pytest.mark.django_db
def test_inbox_strips_nested_media(client, remote_actor, keyholder):
    Instance.objects.filter(domain="other.test").update(reject_media=True)
    activity = {
        "id": "https://other.test/activities/16",
        "type": "Update",
        "actor": REMOTE,
        "object": {
            "type": "Note",
            "attachment": [{"url": "x"}],
            "nested": {"attachment": [{"url": "y"}]},
        },
    }
    _post(client, reverse("shared-inbox"), activity, keyholder)
    stored = Activity.objects.get(ap_id=activity["id"])
    assert "attachment" not in stored.payload["object"]
    assert "attachment" not in stored.payload["object"]["nested"]
