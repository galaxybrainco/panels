from datetime import timedelta
from uuid import uuid4

import pytest
import responses
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from actors.services import create_local_actor
from comics.services import create_comic
from memberships.bmac import BmacError, fetch_subscriptions, reconcile_bmac
from memberships.models import (
    Provider,
    ProviderConnection,
    ProviderSupporter,
    SupporterStatus,
    Tier,
)
from memberships.services import apply_supporter_entitlement


def user(email=None):
    return get_user_model().objects.create_user(
        email=email or f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


def scene():
    owner = user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    connection = ProviderConnection(comic=comic, provider=Provider.BMAC)
    connection.set_api_key("api-token")
    connection.set_webhook_token("hook-secret")
    connection.save()
    return owner, comic, connection


@pytest.mark.django_db
def test_apply_entitlement_activates_linked_status():
    _, comic, connection = scene()
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    from memberships.services import link_supporter

    link_supporter(actor, comic, Provider.BMAC, "reader@example.com")
    supporter = ProviderSupporter.objects.create(
        connection=connection,
        email="reader@example.com",
        tier_name="Fan",
        active=True,
        valid_until=timezone.now() + timedelta(days=10),
    )
    apply_supporter_entitlement(supporter)
    assert SupporterStatus.objects.filter(link__actor=actor, tier=tier).exists()


@pytest.mark.django_db
def test_apply_entitlement_deactivates_status_when_supporter_inactive():
    _, comic, connection = scene()
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    from memberships.services import link_supporter

    link = link_supporter(actor, comic, Provider.BMAC, "reader@example.com")
    SupporterStatus.objects.create(link=link, tier=tier)
    supporter = ProviderSupporter.objects.create(
        connection=connection,
        email="reader@example.com",
        tier_name="Fan",
        active=False,
    )
    apply_supporter_entitlement(supporter)
    assert not SupporterStatus.objects.filter(link=link, active=True).exists()


API_URL = "https://developers.buymeacoffee.com/api/v1/subscriptions"


def subscription(
    email="reader@example.com",
    tier="Fan",
    status="active",
    end="2030-01-01T00:00:00Z",
):
    return {
        "email": email,
        "membership_level_name": tier,
        "status": status,
        "current_period_end": end,
    }


@responses.activate
def test_fetch_subscriptions_parses_and_paginates():
    page_two = "https://developers.buymeacoffee.com/api/v1/subscriptions?page=2"
    responses.add(
        responses.GET,
        API_URL,
        json={"data": [subscription()], "next_page_url": page_two},
        status=200,
    )
    responses.add(
        responses.GET,
        page_two,
        json={"data": [subscription(email="two@example.com")]},
        status=200,
    )
    result = fetch_subscriptions("token")
    assert [item["email"] for item in result] == [
        "reader@example.com",
        "two@example.com",
    ]
    assert responses.calls[0].request.headers["Authorization"] == "Bearer token"


@responses.activate
def test_fetch_subscriptions_raises_on_http_error():
    responses.add(responses.GET, API_URL, status=500)
    with pytest.raises(BmacError):
        fetch_subscriptions("token")


@responses.activate
@pytest.mark.django_db
def test_reconcile_activates_then_deactivates_missing():
    _, comic, connection = scene()
    Tier.objects.create(comic=comic, name="Fan", rank=1)
    responses.add(responses.GET, API_URL, json={"data": [subscription()]}, status=200)
    reconcile_bmac(connection)
    supporter = ProviderSupporter.objects.get(
        connection=connection, email="reader@example.com"
    )
    assert supporter.active is True
    assert supporter.tier_name == "Fan"
    assert supporter.valid_until is not None

    responses.reset()
    responses.add(responses.GET, API_URL, json={"data": []}, status=200)
    reconcile_bmac(connection)
    supporter.refresh_from_db()
    assert supporter.active is False


@responses.activate
@pytest.mark.django_db
def test_reconcile_failure_makes_no_changes():
    _, _, connection = scene()
    supporter = ProviderSupporter.objects.create(
        connection=connection, email="reader@example.com", active=True
    )
    responses.add(responses.GET, API_URL, status=503)
    with pytest.raises(BmacError):
        reconcile_bmac(connection)
    supporter.refresh_from_db()
    assert supporter.active is True


@responses.activate
@pytest.mark.django_db
def test_reconcile_refreshes_linked_access_and_case_insensitive_tier():
    _, comic, connection = scene()
    Tier.objects.create(comic=comic, name="fan", rank=1)
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    from memberships.services import link_supporter, supporter_has_access

    link_supporter(actor, comic, Provider.BMAC, "reader@example.com")
    responses.add(
        responses.GET, API_URL, json={"data": [subscription(tier="Fan")]}, status=200
    )
    reconcile_bmac(connection)
    assert supporter_has_access(actor, comic) is True

    responses.reset()
    responses.add(responses.GET, API_URL, json={"data": []}, status=200)
    reconcile_bmac(connection)
    assert supporter_has_access(actor, comic) is False


@pytest.mark.django_db
def test_bmac_webhook_enqueues_reconcile(client):
    _, comic, connection = scene()
    response = client.post(f"/webhooks/bmac/{comic.id}/hook-secret")
    assert response.status_code == 200
    assert DBTaskResult.objects.filter(
        task_path__contains="reconcile_bmac_comic"
    ).exists()


@pytest.mark.django_db
def test_bmac_webhook_rejects_bad_secret_and_unknown_comic(client):
    _, comic, connection = scene()
    assert client.post(f"/webhooks/bmac/{comic.id}/wrong").status_code == 403
    from uuid import uuid4 as new_uuid

    assert client.post(f"/webhooks/bmac/{new_uuid()}/hook-secret").status_code == 404
    assert client.get(f"/webhooks/bmac/{comic.id}/hook-secret").status_code == 405


@responses.activate
@pytest.mark.django_db
def test_reconcile_command_updates_bmac_connections():
    _, comic, connection = scene()
    Tier.objects.create(comic=comic, name="Fan", rank=1)
    responses.add(responses.GET, API_URL, json={"data": [subscription()]}, status=200)
    call_command("reconcile_bmac")
    assert ProviderSupporter.objects.filter(
        connection=connection, email="reader@example.com", active=True
    ).exists()


@responses.activate
@pytest.mark.django_db
def test_reconcile_pagination_cap_aborts_without_changes(monkeypatch):
    _, comic, connection = scene()
    supporter = ProviderSupporter.objects.create(
        connection=connection,
        email="reader@example.com",
        tier_name="Fan",
        active=True,
        valid_until=timezone.now() + timedelta(days=30),
    )
    monkeypatch.setattr("memberships.bmac.MAX_PAGES", 1)
    responses.add(
        responses.GET,
        API_URL,
        json={"data": [], "next_page_url": f"{API_URL}?page=2"},
        status=200,
    )
    with pytest.raises(BmacError):
        reconcile_bmac(connection)
    supporter.refresh_from_db()
    assert supporter.active is True


@responses.activate
def test_fetch_subscriptions_detects_pagination_cycle():
    page_two = f"{API_URL}?page=2"
    responses.add(
        responses.GET, API_URL, json={"data": [], "next_page_url": page_two}, status=200
    )
    responses.add(
        responses.GET, page_two, json={"data": [], "next_page_url": API_URL}, status=200
    )
    with pytest.raises(BmacError, match="cycle"):
        fetch_subscriptions("key")


@responses.activate
@pytest.mark.django_db
def test_reconcile_skips_active_subscription_without_valid_period_end():
    _, comic, connection = scene()
    Tier.objects.create(comic=comic, name="Fan", rank=1)
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    from memberships.services import link_supporter, supporter_has_access

    link_supporter(actor, comic, Provider.BMAC, "reader@example.com")
    supporter = ProviderSupporter.objects.create(
        connection=connection,
        email="reader@example.com",
        tier_name="Fan",
        active=True,
        valid_until=timezone.now() + timedelta(days=30),
    )
    responses.add(
        responses.GET, API_URL, json={"data": [subscription(end=None)]}, status=200
    )
    reconcile_bmac(connection)
    supporter.refresh_from_db()
    assert supporter.active is False
    assert supporter_has_access(actor, comic) is False


@pytest.mark.django_db
def test_attach_pending_supporter_preserves_manual_status_for_expired_supporter():
    from memberships.kofi import attach_pending_supporter
    from memberships.services import (
        grant_supporter_status,
        link_supporter,
        supporter_has_access,
    )

    _, comic, connection = scene()
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    link = link_supporter(actor, comic, Provider.BMAC, "reader@example.com")
    grant_supporter_status(link, tier)
    ProviderSupporter.objects.create(
        connection=connection,
        email="reader@example.com",
        tier_name="Fan",
        active=True,
        valid_until=timezone.now() - timedelta(days=1),
    )
    attach_pending_supporter(actor, comic, Provider.BMAC, "reader@example.com")
    assert supporter_has_access(actor, comic) is True
