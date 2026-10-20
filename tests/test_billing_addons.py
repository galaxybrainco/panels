import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
import responses
from django.contrib.auth import get_user_model
from django.test import override_settings

from billing import quota
from billing.models import ComicPlan, PlanStatus
from comics.models import ComicRole
from comics.services import create_comic

ADDON_PRICE = "price_addon"
BASE_PRICE = "price_base"
BLOCK = 10 * 1024**3
BASE = 10 * 1024**3
WEBHOOK_SECRET = "whsec_test"

ADDON_SETTINGS = {
    "BILLING_ENABLED": True,
    "STRIPE_SECRET_KEY": "sk_test_123",
    "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET,
    "BILLING_PRICE_MONTHLY": BASE_PRICE,
    "BILLING_PRICE_ANNUAL": "price_year",
    "BILLING_STORAGE_ADDON_PRICE": ADDON_PRICE,
    "BILLING_STORAGE_ADDON_BYTES": BLOCK,
    "BILLING_STORAGE_ADDON_MAX_UNITS": 5,
    "MEDIA_STORAGE_QUOTA_BYTES": BASE,
}


def _scene():
    owner = get_user_model().objects.create_user(
        email=f"o-{uuid4().hex[:8]}@example.com", password="x"
    )
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    return owner, comic


def _item(item_id, price, quantity):
    return {"id": item_id, "price": {"id": price}, "quantity": quantity}


def _subscription(comic, items=None, status="active"):
    return {
        "id": "sub_1",
        "object": "subscription",
        "customer": "cus_1",
        "status": status,
        "metadata": {"comic_id": str(comic.id)},
        "current_period_end": 1893456000,
        "items": {"data": items if items is not None else []},
    }


def _signature(payload, secret=WEBHOOK_SECRET):
    timestamp = int(time.time())
    digest = hmac.new(
        secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _post(client, event_type, obj):
    payload = json.dumps(
        {
            "id": "evt_1",
            "object": "event",
            "type": event_type,
            "data": {"object": obj},
        }
    )
    return client.post(
        "/billing/webhook",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_signature(payload),
    )


@override_settings(**ADDON_SETTINGS)
def test_quota_math_sums_addon_items():
    subscription = {
        "items": {
            "data": [
                _item("si_base", BASE_PRICE, 1),
                _item("si_add", ADDON_PRICE, 2),
                _item("si_other", "price_other", 9),
            ]
        }
    }
    assert quota.addon_item(subscription) == ("si_add", 2)
    assert quota.addon_units(subscription) == 2
    assert quota.quota_bytes_for(subscription) == BASE + 2 * BLOCK


@override_settings(**{**ADDON_SETTINGS, "BILLING_STORAGE_ADDON_PRICE": ""})
def test_quota_math_ignores_addons_when_unconfigured():
    subscription = {"items": {"data": [_item("si_add", ADDON_PRICE, 2)]}}
    assert quota.addon_item(subscription) == ("", 0)
    assert quota.quota_bytes_for(subscription) == BASE


def test_quota_math_handles_missing_items():
    assert quota.addon_item({}) == ("", 0)
    assert quota.quota_bytes_for({}) == BASE


@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_webhook_sets_addon_quota_and_item_id(client):
    _, comic = _scene()
    subscription = _subscription(
        comic, [_item("si_base", BASE_PRICE, 1), _item("si_add", ADDON_PRICE, 2)]
    )
    assert (
        _post(client, "customer.subscription.created", subscription).status_code == 200
    )
    plan = ComicPlan.objects.get(comic=comic)
    assert plan.quota_bytes == BASE + 2 * BLOCK
    assert plan.stripe_addon_item_id == "si_add"


@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_webhook_removal_restores_base_quota(client):
    _, comic = _scene()
    _post(
        client,
        "customer.subscription.created",
        _subscription(comic, [_item("si_add", ADDON_PRICE, 2)]),
    )
    _post(
        client,
        "customer.subscription.updated",
        _subscription(comic, [_item("si_base", BASE_PRICE, 1)]),
    )
    plan = ComicPlan.objects.get(comic=comic)
    assert plan.quota_bytes == BASE
    assert plan.stripe_addon_item_id == ""


@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_webhook_tracks_operator_base_quota_change(client):
    _, comic = _scene()
    subscription = _subscription(comic, [_item("si_add", ADDON_PRICE, 1)])
    _post(client, "customer.subscription.created", subscription)
    with override_settings(MEDIA_STORAGE_QUOTA_BYTES=42):
        _post(client, "customer.subscription.updated", subscription)
    assert ComicPlan.objects.get(comic=comic).quota_bytes == 42 + BLOCK


@override_settings(**{**ADDON_SETTINGS, "BILLING_STORAGE_ADDON_PRICE": ""})
@pytest.mark.django_db
def test_webhook_ignores_unrelated_items_when_unconfigured(client):
    _, comic = _scene()
    _post(
        client,
        "customer.subscription.created",
        _subscription(comic, [_item("si_add", ADDON_PRICE, 2)]),
    )
    plan = ComicPlan.objects.get(comic=comic)
    assert plan.quota_bytes == BASE
    assert plan.stripe_addon_item_id == ""


STRIPE_API = "https://api.stripe.com/v1"


def _checkout_scene(client):
    owner, comic = _scene()
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
        json={
            "id": "cs_123",
            "object": "checkout.session",
            "url": "https://checkout.stripe.test/cs_123",
        },
        status=200,
    )
    return owner, comic


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_checkout_adds_storage_line_item(client):
    _, comic = _checkout_scene(client)
    response = client.post(
        f"/comics/{comic.id}/billing/checkout",
        data=json.dumps({"addon_units": 2}),
        content_type="application/json",
    )
    assert response.status_code == 200
    body = parse_qs(responses.calls[1].request.body)
    assert body["line_items[1][price]"] == [ADDON_PRICE]
    assert body["line_items[1][quantity]"] == ["2"]


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_checkout_without_addons_has_one_line_item(client):
    _, comic = _checkout_scene(client)
    response = client.post(f"/comics/{comic.id}/billing/checkout")
    assert response.status_code == 200
    body = parse_qs(responses.calls[1].request.body)
    assert "line_items[1][price]" not in body


@pytest.mark.parametrize("units", ["2", -1, 6, True, 1.5])
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_checkout_rejects_invalid_addon_units(client, units):
    owner, comic = _scene()
    client.force_login(owner)
    response = client.post(
        f"/comics/{comic.id}/billing/checkout",
        data=json.dumps({"addon_units": units}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_units"}


@override_settings(**{**ADDON_SETTINGS, "BILLING_STORAGE_ADDON_PRICE": ""})
@pytest.mark.django_db
def test_checkout_rejects_addons_when_unconfigured(client):
    owner, comic = _scene()
    client.force_login(owner)
    response = client.post(
        f"/comics/{comic.id}/billing/checkout",
        data=json.dumps({"addon_units": 2}),
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json() == {"error": "addons_unavailable"}


def _subscribed_plan(comic, **kwargs):
    status = kwargs.pop("status", PlanStatus.ACTIVE)
    kwargs.setdefault("stripe_subscription_id", "sub_1")
    return ComicPlan.objects.create(
        comic=comic, status=status, quota_bytes=BASE, **kwargs
    )


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_creates_item_when_none_exists(client):
    owner, comic = _scene()
    _subscribed_plan(comic)
    client.force_login(owner)
    responses.add(
        responses.GET,
        f"{STRIPE_API}/subscriptions/sub_1",
        json={"id": "sub_1", "object": "subscription", "items": {"data": []}},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{STRIPE_API}/subscription_items",
        json={"id": "si_new", "object": "subscription_item"},
        status=200,
    )

    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 3}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {"units": 3}
    assert ComicPlan.objects.get(comic=comic).stripe_addon_item_id == "si_new"
    body = parse_qs(responses.calls[1].request.body)
    assert body["subscription"] == ["sub_1"]
    assert body["price"] == [ADDON_PRICE]
    assert body["quantity"] == ["3"]


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_modifies_stored_item(client):
    owner, comic = _scene()
    _subscribed_plan(comic, stripe_addon_item_id="si_add")
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/subscription_items/si_add",
        json={"id": "si_add", "object": "subscription_item"},
        status=200,
    )

    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 2}),
        content_type="application/json",
    )

    assert response.status_code == 200
    body = parse_qs(responses.calls[0].request.body)
    assert body["quantity"] == ["2"]


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_zero_deletes_item_and_clears_id(client):
    owner, comic = _scene()
    _subscribed_plan(comic, stripe_addon_item_id="si_add")
    client.force_login(owner)
    responses.add(
        responses.GET,
        f"{STRIPE_API}/subscriptions/sub_1",
        json={
            "id": "sub_1",
            "object": "subscription",
            "items": {
                "data": [{"id": "si_add", "price": {"id": ADDON_PRICE}, "quantity": 2}]
            },
        },
        status=200,
    )
    responses.add(
        responses.DELETE,
        f"{STRIPE_API}/subscription_items/si_add",
        json={"id": "si_add", "object": "subscription_item", "deleted": True},
        status=200,
    )

    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 0}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {"units": 0}
    assert len(responses.calls) == 2
    assert ComicPlan.objects.get(comic=comic).stripe_addon_item_id == ""


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_can_delete_then_readd(client):
    owner, comic = _scene()
    _subscribed_plan(comic, stripe_addon_item_id="si_add")
    client.force_login(owner)
    responses.add(
        responses.GET,
        f"{STRIPE_API}/subscriptions/sub_1",
        json={
            "id": "sub_1",
            "object": "subscription",
            "items": {
                "data": [{"id": "si_add", "price": {"id": ADDON_PRICE}, "quantity": 2}]
            },
        },
        status=200,
    )
    responses.add(
        responses.DELETE,
        f"{STRIPE_API}/subscription_items/si_add",
        json={"id": "si_add", "object": "subscription_item", "deleted": True},
        status=200,
    )
    assert (
        client.post(
            f"/comics/{comic.id}/billing/addons",
            data=json.dumps({"units": 0}),
            content_type="application/json",
        ).status_code
        == 200
    )

    responses.reset()
    responses.add(
        responses.GET,
        f"{STRIPE_API}/subscriptions/sub_1",
        json={"id": "sub_1", "object": "subscription", "items": {"data": []}},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{STRIPE_API}/subscription_items",
        json={"id": "si_new", "object": "subscription_item"},
        status=200,
    )
    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 2}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert ComicPlan.objects.get(comic=comic).stripe_addon_item_id == "si_new"


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_recovers_from_stale_item_id(client):
    owner, comic = _scene()
    _subscribed_plan(comic, stripe_addon_item_id="si_gone")
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/subscription_items/si_gone",
        json={"error": {"message": "No such subscription item"}},
        status=404,
    )
    responses.add(
        responses.GET,
        f"{STRIPE_API}/subscriptions/sub_1",
        json={
            "id": "sub_1",
            "object": "subscription",
            "items": {
                "data": [{"id": "si_real", "price": {"id": ADDON_PRICE}, "quantity": 1}]
            },
        },
        status=200,
    )
    responses.add(
        responses.POST,
        f"{STRIPE_API}/subscription_items/si_real",
        json={"id": "si_real", "object": "subscription_item"},
        status=200,
    )

    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 2}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert ComicPlan.objects.get(comic=comic).stripe_addon_item_id == "si_real"


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_zero_removes_all_matching_items(client):
    owner, comic = _scene()
    _subscribed_plan(comic, stripe_addon_item_id="si_one")
    client.force_login(owner)
    responses.add(
        responses.GET,
        f"{STRIPE_API}/subscriptions/sub_1",
        json={
            "id": "sub_1",
            "object": "subscription",
            "items": {
                "data": [
                    {"id": "si_one", "price": {"id": ADDON_PRICE}, "quantity": 1},
                    {"id": "si_two", "price": {"id": ADDON_PRICE}, "quantity": 1},
                ]
            },
        },
        status=200,
    )
    for item_id in ("si_one", "si_two"):
        responses.add(
            responses.DELETE,
            f"{STRIPE_API}/subscription_items/{item_id}",
            json={"id": item_id, "object": "subscription_item", "deleted": True},
            status=200,
        )

    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 0}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert ComicPlan.objects.get(comic=comic).stripe_addon_item_id == ""
    deletes = [call for call in responses.calls if call.request.method == "DELETE"]
    assert len(deletes) == 2


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_zero_without_item_is_a_noop(client):
    owner, comic = _scene()
    _subscribed_plan(comic)
    client.force_login(owner)
    responses.add(
        responses.GET,
        f"{STRIPE_API}/subscriptions/sub_1",
        json={"id": "sub_1", "object": "subscription", "items": {"data": []}},
        status=200,
    )

    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 0}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert len(responses.calls) == 1


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_falls_back_to_retrieving_the_item(client):
    owner, comic = _scene()
    _subscribed_plan(comic)
    client.force_login(owner)
    responses.add(
        responses.GET,
        f"{STRIPE_API}/subscriptions/sub_1",
        json={
            "id": "sub_1",
            "object": "subscription",
            "items": {
                "data": [
                    {"id": "si_found", "price": {"id": ADDON_PRICE}, "quantity": 1}
                ]
            },
        },
        status=200,
    )
    responses.add(
        responses.POST,
        f"{STRIPE_API}/subscription_items/si_found",
        json={"id": "si_found", "object": "subscription_item"},
        status=200,
    )

    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 4}),
        content_type="application/json",
    )

    assert response.status_code == 200
    plan = ComicPlan.objects.get(comic=comic)
    assert plan.stripe_addon_item_id == "si_found"


@pytest.mark.parametrize("units", ["2", -1, 6, True, 1.5])
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_rejects_invalid_units(client, units):
    owner, comic = _scene()
    _subscribed_plan(comic)
    client.force_login(owner)
    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": units}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_units"}


@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_requires_units(client):
    owner, comic = _scene()
    _subscribed_plan(comic)
    client.force_login(owner)
    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_units"}


@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_requires_a_subscription(client):
    owner, comic = _scene()
    _subscribed_plan(comic, stripe_subscription_id="")
    client.force_login(owner)
    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 1}),
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json() == {"error": "no_subscription"}


@override_settings(**{**ADDON_SETTINGS, "BILLING_STORAGE_ADDON_PRICE": ""})
@pytest.mark.django_db
def test_addons_unavailable_when_unconfigured(client):
    owner, comic = _scene()
    _subscribed_plan(comic)
    client.force_login(owner)
    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 1}),
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json() == {"error": "addons_unavailable"}


@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_rejects_non_owner(client):
    _, comic = _scene()
    _subscribed_plan(comic)
    collaborator = get_user_model().objects.create_user(
        email="editor@example.com", password="x"
    )
    ComicRole.objects.create(comic=comic, user=collaborator, role=ComicRole.Role.EDITOR)
    client.force_login(collaborator)
    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 1}),
        content_type="application/json",
    )
    assert response.status_code == 403
    assert response.json() == {"error": "not_owner"}


@override_settings(**{**ADDON_SETTINGS, "BILLING_ENABLED": False})
@pytest.mark.django_db
def test_addons_hidden_when_billing_disabled(client):
    _, comic = _scene()
    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 1}),
        content_type="application/json",
    )
    assert response.status_code == 404


@responses.activate
@override_settings(**ADDON_SETTINGS)
@pytest.mark.django_db
def test_addons_stripe_failure_returns_502(client):
    owner, comic = _scene()
    _subscribed_plan(comic, stripe_addon_item_id="si_add")
    client.force_login(owner)
    responses.add(
        responses.POST,
        f"{STRIPE_API}/subscription_items/si_add",
        json={"error": {"message": "boom"}},
        status=500,
    )
    response = client.post(
        f"/comics/{comic.id}/billing/addons",
        data=json.dumps({"units": 2}),
        content_type="application/json",
    )
    assert response.status_code == 502
    assert response.json() == {"error": "stripe_error"}
