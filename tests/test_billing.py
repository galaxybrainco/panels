import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
import responses
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import override_settings

from billing import webhooks
from billing.models import BillingCustomer, ComicPlan, PlanStatus
from comics.models import ComicRole
from comics.services import create_comic

STRIPE_API = "https://api.stripe.com/v1"
PRICE_MONTHLY = "price_month"
PRICE_ANNUAL = "price_year"
WEBHOOK_SECRET = "whsec_test"
BILLING_SETTINGS = {
    "BILLING_ENABLED": True,
    "STRIPE_SECRET_KEY": "sk_test_123",
    "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET,
    "BILLING_PRICE_MONTHLY": PRICE_MONTHLY,
    "BILLING_PRICE_ANNUAL": PRICE_ANNUAL,
    "BILLING_SUCCESS_URL": "https://panels.test/ok",
    "BILLING_CANCEL_URL": "https://panels.test/cancel",
    "BILLING_PORTAL_RETURN_URL": "https://panels.test/back",
}


def _user():
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


@pytest.mark.django_db
def test_billing_customer_is_one_per_user_with_unique_stripe_id():
    user = _user()
    BillingCustomer.objects.create(user=user, stripe_customer_id="cus_1")
    assert user.billing_customer.stripe_customer_id == "cus_1"
    other = _user()
    with pytest.raises(IntegrityError), transaction.atomic():
        BillingCustomer.objects.create(user=other, stripe_customer_id="cus_1")
    with pytest.raises(IntegrityError), transaction.atomic():
        BillingCustomer.objects.create(user=user, stripe_customer_id="cus_2")


def _comic_scene():
    owner = _user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    return owner, comic


def _session_response():
    return {
        "id": "cs_123",
        "object": "checkout.session",
        "url": "https://checkout.stripe.test/cs_123",
    }


@responses.activate
@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_checkout_creates_customer_and_returns_url(client):
    owner, comic = _comic_scene()
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/customers",
        json={"id": "cus_123", "object": "customer"},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{STRIPE_API}/checkout/sessions",
        json=_session_response(),
        status=200,
    )

    response = client.post(f"/comics/{comic.id}/billing/checkout")

    assert response.status_code == 200
    assert response.json() == {"checkout_url": "https://checkout.stripe.test/cs_123"}
    account = BillingCustomer.objects.get(user=owner)
    assert account.stripe_customer_id == "cus_123"
    assert responses.calls[0].request.headers["Authorization"] == "Bearer sk_test_123"
    body = parse_qs(responses.calls[1].request.body)
    assert body["customer"] == ["cus_123"]
    assert body["line_items[0][price]"] == [PRICE_MONTHLY]
    assert body["client_reference_id"] == [str(comic.id)]
    assert body["subscription_data[metadata][comic_id]"] == [str(comic.id)]
    assert body["success_url"] == ["https://panels.test/ok"]
    assert body["cancel_url"] == ["https://panels.test/cancel"]


@responses.activate
@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_checkout_reuses_existing_customer(client):
    owner, comic = _comic_scene()
    BillingCustomer.objects.create(user=owner, stripe_customer_id="cus_existing")
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/checkout/sessions",
        json=_session_response(),
        status=200,
    )

    response = client.post(f"/comics/{comic.id}/billing/checkout")

    assert response.status_code == 200
    assert len(responses.calls) == 1
    body = parse_qs(responses.calls[0].request.body)
    assert body["customer"] == ["cus_existing"]


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_checkout_rejects_non_owner(client):
    _, comic = _comic_scene()
    collaborator = _user()
    ComicRole.objects.create(comic=comic, user=collaborator, role=ComicRole.Role.EDITOR)
    client.force_login(collaborator)

    response = client.post(f"/comics/{comic.id}/billing/checkout")

    assert response.status_code == 403
    assert response.json() == {"error": "not_owner"}


@override_settings(**{**BILLING_SETTINGS, "BILLING_ENABLED": False})
@pytest.mark.django_db
def test_checkout_is_hidden_when_billing_disabled(client):
    owner, comic = _comic_scene()
    client.force_login(owner)
    response = client.post(f"/comics/{comic.id}/billing/checkout")
    assert response.status_code == 404


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_checkout_rejects_invalid_interval(client):
    owner, comic = _comic_scene()
    client.force_login(owner)
    response = client.post(
        f"/comics/{comic.id}/billing/checkout",
        data=json.dumps({"interval": "week"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_interval"}


@override_settings(**{**BILLING_SETTINGS, "BILLING_PRICE_ANNUAL": ""})
@pytest.mark.django_db
def test_checkout_rejects_unconfigured_annual_plan(client):
    owner, comic = _comic_scene()
    client.force_login(owner)
    response = client.post(
        f"/comics/{comic.id}/billing/checkout",
        data=json.dumps({"interval": "year"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_interval"}


@responses.activate
@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_checkout_uses_annual_price(client):
    owner, comic = _comic_scene()
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/customers",
        json={"id": "cus_123", "object": "customer"},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{STRIPE_API}/checkout/sessions",
        json=_session_response(),
        status=200,
    )

    response = client.post(
        f"/comics/{comic.id}/billing/checkout",
        data=json.dumps({"interval": "year"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    body = parse_qs(responses.calls[1].request.body)
    assert body["line_items[0][price]"] == [PRICE_ANNUAL]


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_checkout_rejects_active_plan(client):
    owner, comic = _comic_scene()
    ComicPlan.objects.create(comic=comic, status=PlanStatus.ACTIVE, quota_bytes=123)
    client.force_login(owner)
    response = client.post(f"/comics/{comic.id}/billing/checkout")
    assert response.status_code == 409
    assert response.json() == {"error": "already_subscribed"}


@override_settings(**{**BILLING_SETTINGS, "BILLING_PRICE_MONTHLY": ""})
@pytest.mark.django_db
def test_checkout_fails_closed_without_monthly_price(client):
    owner, comic = _comic_scene()
    client.force_login(owner)
    response = client.post(f"/comics/{comic.id}/billing/checkout")
    assert response.status_code == 502
    assert response.json() == {"error": "stripe_error"}
    assert not BillingCustomer.objects.filter(user=owner).exists()


@responses.activate
@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_checkout_stripe_failure_returns_502_without_residue(client):
    owner, comic = _comic_scene()
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/customers",
        json={"error": {"message": "boom"}},
        status=500,
    )

    response = client.post(f"/comics/{comic.id}/billing/checkout")

    assert response.status_code == 502
    assert response.json() == {"error": "stripe_error"}
    assert not BillingCustomer.objects.filter(user=owner).exists()


@responses.activate
@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_portal_returns_url_for_existing_customer(client):
    owner, comic = _comic_scene()
    BillingCustomer.objects.create(user=owner, stripe_customer_id="cus_123")
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/billing_portal/sessions",
        json={
            "id": "bps_1",
            "object": "billing_portal.session",
            "url": "https://billing.stripe.test/bps_1",
        },
        status=200,
    )

    response = client.post(f"/comics/{comic.id}/billing/portal")

    assert response.status_code == 200
    assert response.json() == {"portal_url": "https://billing.stripe.test/bps_1"}
    body = parse_qs(responses.calls[0].request.body)
    assert body["customer"] == ["cus_123"]
    assert body["return_url"] == ["https://panels.test/back"]


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_portal_without_customer_returns_409(client):
    owner, comic = _comic_scene()
    client.force_login(owner)
    response = client.post(f"/comics/{comic.id}/billing/portal")
    assert response.status_code == 409
    assert response.json() == {"error": "no_billing_account"}


@override_settings(**{**BILLING_SETTINGS, "BILLING_ENABLED": False})
@pytest.mark.django_db
def test_portal_is_hidden_when_billing_disabled(client):
    owner, comic = _comic_scene()
    client.force_login(owner)
    response = client.post(f"/comics/{comic.id}/billing/portal")
    assert response.status_code == 404


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_portal_rejects_non_owner(client):
    _, comic = _comic_scene()
    collaborator = _user()
    ComicRole.objects.create(comic=comic, user=collaborator, role=ComicRole.Role.EDITOR)
    client.force_login(collaborator)
    response = client.post(f"/comics/{comic.id}/billing/portal")
    assert response.status_code == 403
    assert response.json() == {"error": "not_owner"}


@responses.activate
@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_portal_stripe_failure_returns_502(client):
    owner, comic = _comic_scene()
    BillingCustomer.objects.create(user=owner, stripe_customer_id="cus_123")
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/billing_portal/sessions",
        json={"error": {"message": "boom"}},
        status=500,
    )
    response = client.post(f"/comics/{comic.id}/billing/portal")
    assert response.status_code == 502
    assert response.json() == {"error": "stripe_error"}


def _signature(payload, secret=WEBHOOK_SECRET, timestamp=None):
    timestamp = timestamp or int(time.time())
    digest = hmac.new(
        secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _event(
    event_type,
    comic=None,
    *,
    status="active",
    subscription_id="sub_1",
    customer="cus_1",
    period_end=1893456000,
    metadata=None,
):
    subscription = {
        "id": subscription_id,
        "object": "subscription",
        "customer": customer,
        "status": status,
        "metadata": {"comic_id": str(comic.id)} if comic else (metadata or {}),
    }
    if period_end is not None:
        subscription["current_period_end"] = period_end
    return json.dumps(
        {
            "id": "evt_1",
            "object": "event",
            "type": event_type,
            "data": {"object": subscription},
        }
    )


def _post_event(client, event_type, comic=None, **kwargs):
    payload = _event(event_type, comic, **kwargs)
    return client.post(
        "/billing/webhook",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_signature(payload),
    )


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_webhook_creates_active_plan(client):
    _, comic = _comic_scene()
    response = _post_event(client, "customer.subscription.created", comic)
    assert response.status_code == 200
    assert response.json() == {"received": True}
    plan = ComicPlan.objects.get(comic=comic)
    assert plan.status == PlanStatus.ACTIVE
    assert plan.stripe_customer_id == "cus_1"
    assert plan.stripe_subscription_id == "sub_1"
    assert plan.quota_bytes == settings.MEDIA_STORAGE_QUOTA_BYTES
    assert plan.current_period_end == datetime(2030, 1, 1, tzinfo=UTC)


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_webhook_maps_trialing_and_past_due(client):
    _, comic = _comic_scene()
    _post_event(client, "customer.subscription.created", comic, status="trialing")
    assert ComicPlan.objects.get(comic=comic).status == PlanStatus.ACTIVE
    _post_event(client, "customer.subscription.updated", comic, status="past_due")
    assert ComicPlan.objects.get(comic=comic).status == PlanStatus.PAST_DUE
    _post_event(client, "customer.subscription.updated", comic, status="canceled")
    assert ComicPlan.objects.get(comic=comic).status == PlanStatus.PAST_DUE


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_webhook_deletion_marks_plan_past_due(client):
    _, comic = _comic_scene()
    _post_event(client, "customer.subscription.created", comic)
    response = _post_event(
        client,
        "customer.subscription.deleted",
        comic,
        status="canceled",
        subscription_id="sub_1",
    )
    assert response.status_code == 200
    assert ComicPlan.objects.get(comic=comic).status == PlanStatus.PAST_DUE


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_webhook_preserves_quota_on_update(client):
    _, comic = _comic_scene()
    ComicPlan.objects.create(comic=comic, status=PlanStatus.ACTIVE, quota_bytes=12_345)
    _post_event(client, "customer.subscription.updated", comic)
    assert ComicPlan.objects.get(comic=comic).quota_bytes == 12_345


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_webhook_is_idempotent(client):
    _, comic = _comic_scene()
    _post_event(client, "customer.subscription.created", comic)
    _post_event(client, "customer.subscription.created", comic)
    assert ComicPlan.objects.filter(comic=comic).count() == 1


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_webhook_reads_period_end_from_subscription_items(client):
    _, comic = _comic_scene()
    payload = json.dumps(
        {
            "id": "evt_1",
            "object": "event",
            "type": "customer.subscription.created",
            "data": {
                "object": {
                    "id": "sub_1",
                    "object": "subscription",
                    "customer": "cus_1",
                    "status": "active",
                    "metadata": {"comic_id": str(comic.id)},
                    "items": {"data": [{"current_period_end": 1893456000}]},
                }
            },
        }
    )
    response = client.post(
        "/billing/webhook",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_signature(payload),
    )
    assert response.status_code == 200
    assert ComicPlan.objects.get(comic=comic).current_period_end == datetime(
        2030, 1, 1, tzinfo=UTC
    )


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_webhook_rejects_bad_signature_without_writes(client):
    _, comic = _comic_scene()
    payload = _event("customer.subscription.created", comic)
    response = client.post(
        "/billing/webhook",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE="t=1,v1=deadbeef",
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_signature"}
    assert not ComicPlan.objects.filter(comic=comic).exists()


@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_webhook_ignores_unknown_events_comics_and_metadata(client):
    _, comic = _comic_scene()
    assert _post_event(client, "invoice.paid", comic).status_code == 200
    assert (
        _post_event(
            client,
            "customer.subscription.created",
            metadata={"comic_id": "not-a-uuid"},
        ).status_code
        == 200
    )
    assert (
        _post_event(
            client,
            "customer.subscription.created",
            metadata={"comic_id": str(uuid4())},
        ).status_code
        == 200
    )
    assert (
        _post_event(client, "customer.subscription.created", metadata={}).status_code
        == 200
    )
    assert not ComicPlan.objects.filter(comic=comic).exists()


@override_settings(**{**BILLING_SETTINGS, "STRIPE_WEBHOOK_SECRET": ""})
@pytest.mark.django_db
def test_webhook_rejects_when_secret_is_unconfigured(client):
    payload = _event("customer.subscription.created")
    response = client.post(
        "/billing/webhook",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_signature(payload, secret="anything"),
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_signature"}


@override_settings(**{**BILLING_SETTINGS, "BILLING_ENABLED": False})
@pytest.mark.django_db
def test_webhook_is_hidden_when_billing_disabled(client):
    payload = _event("customer.subscription.created")
    response = client.post(
        "/billing/webhook",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_signature(payload),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_plan_status_mapping_covers_known_statuses():
    assert webhooks.plan_status_for("active") == PlanStatus.ACTIVE
    assert webhooks.plan_status_for("trialing") == PlanStatus.ACTIVE
    for status in (
        "past_due",
        "unpaid",
        "incomplete",
        "incomplete_expired",
        "paused",
        "canceled",
        "unknown",
    ):
        assert webhooks.plan_status_for(status) == PlanStatus.PAST_DUE


@responses.activate
@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_checkout_two_owners_each_get_a_customer(client):
    owner_a = _user()
    comic_a = create_comic(owner_a, f"comic{uuid4().hex[:6]}", "One")
    owner_b = _user()
    comic_b = create_comic(owner_b, f"comic{uuid4().hex[:6]}", "Two")
    for customer_id, session_id in (("cus_a", "cs_a"), ("cus_b", "cs_b")):
        responses.add(
            responses.POST,
            f"{STRIPE_API}/customers",
            json={"id": customer_id, "object": "customer"},
            status=200,
        )
        responses.add(
            responses.POST,
            f"{STRIPE_API}/checkout/sessions",
            json={
                "id": session_id,
                "object": "checkout.session",
                "url": f"https://checkout.stripe.test/{session_id}",
            },
            status=200,
        )

    client.force_login(owner_a)
    assert client.post(f"/comics/{comic_a.id}/billing/checkout").status_code == 200
    client.force_login(owner_b)
    assert client.post(f"/comics/{comic_b.id}/billing/checkout").status_code == 200

    assert BillingCustomer.objects.get(user=owner_a).stripe_customer_id == "cus_a"
    assert BillingCustomer.objects.get(user=owner_b).stripe_customer_id == "cus_b"


@responses.activate
@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_checkout_succeeds_with_a_stale_blank_customer_row(client):
    owner, comic = _comic_scene()
    BillingCustomer.objects.create(user=_user(), stripe_customer_id="")
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/customers",
        json={"id": "cus_123", "object": "customer"},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{STRIPE_API}/checkout/sessions",
        json=_session_response(),
        status=200,
    )

    response = client.post(f"/comics/{comic.id}/billing/checkout")

    assert response.status_code == 200
    assert BillingCustomer.objects.get(user=owner).stripe_customer_id == "cus_123"


@responses.activate
@override_settings(**BILLING_SETTINGS)
@pytest.mark.django_db
def test_checkout_fills_blank_customer_id(client):
    owner, comic = _comic_scene()
    BillingCustomer.objects.create(user=owner, stripe_customer_id="")
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/customers",
        json={"id": "cus_123", "object": "customer"},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{STRIPE_API}/checkout/sessions",
        json=_session_response(),
        status=200,
    )

    response = client.post(f"/comics/{comic.id}/billing/checkout")

    assert response.status_code == 200
    account = BillingCustomer.objects.get(user=owner)
    assert account.stripe_customer_id == "cus_123"


@pytest.mark.django_db
def test_sync_handles_plan_created_after_read(monkeypatch):
    _, comic = _comic_scene()
    ComicPlan.objects.create(comic=comic, status=PlanStatus.PAST_DUE, quota_bytes=99)
    real_filter = ComicPlan.objects.filter

    class NoPlan:
        def first(self):
            return None

    monkeypatch.setattr(ComicPlan.objects, "filter", lambda *a, **k: NoPlan())
    webhooks.sync_subscription(
        {
            "id": "sub_1",
            "object": "subscription",
            "customer": "cus_1",
            "status": "active",
            "metadata": {"comic_id": str(comic.id)},
        }
    )
    monkeypatch.setattr(ComicPlan.objects, "filter", real_filter)
    plan = ComicPlan.objects.get(comic=comic)
    assert plan.status == PlanStatus.ACTIVE
    assert plan.quota_bytes == 99


@override_settings(**{**BILLING_SETTINGS, "BILLING_ENABLED": False})
@pytest.mark.django_db
def test_disabled_billing_hides_urls_before_auth_and_method(client):
    _, comic = _comic_scene()
    assert client.post(f"/comics/{comic.id}/billing/checkout").status_code == 404
    assert client.get(f"/comics/{comic.id}/billing/checkout").status_code == 404
    assert client.post(f"/comics/{comic.id}/billing/portal").status_code == 404
    assert client.get(f"/comics/{comic.id}/billing/portal").status_code == 404
    assert client.get("/billing/webhook").status_code == 404
