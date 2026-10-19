import json
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
import responses
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import override_settings

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
    ComicRole.objects.create(
        comic=comic, user=collaborator, role=ComicRole.Role.EDITOR
    )
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
