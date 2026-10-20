import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from billing import quota
from billing.models import ComicPlan
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
