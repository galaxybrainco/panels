# Storage Add-ons (9c2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let owners buy storage blocks as a second Stripe subscription item, change them by quantity, and keep `ComicPlan.quota_bytes` equal to the operator's base cap plus the subscription's blocks.

**Architecture:** `billing/quota.py` parses subscription items and computes quota; subscription webhooks write quota + add-on item id; checkout and a new add-on endpoint talk to Stripe through the existing `stripe_client` seam, leaving quota updates to the webhook. `media.quota` already reads `plan.quota_bytes`, so enforcement needs no change.

**Tech Stack:** Python 3.14, Django 6.1.1, `stripe` SDK 16 via `responses`-mocked HTTP, pytest.

**Spec:** `docs/superpowers/specs/2026-10-18-storage-addons-design.md`

## Global Constraints

- Python 3.14, Django 6.1.1; run everything through `uv run`.
- `billing/stripe_client.py` remains the only module importing `stripe`.
- Settings are env-driven; never hardcode add-on prices or block sizes.
- Stripe is the source of truth for quota: endpoints never write `quota_bytes`; webhooks always recompute it.
- Validate units/configuration before any Stripe call.
- JSON error codes: `invalid_units`, `invalid_payload`, `addons_unavailable`, `no_subscription`, `not_owner`, `stripe_error`; disabled billing 404s first.
- Boolean values are not integers for validation purposes (`True` is rejected).
- Keep `ruff check .` and `ruff format --check .` clean; run `uv run python manage.py makemigrations --check --dry-run` before final commits.

## Review Focus

- **Quota drift:** subscriptions with no items, missing quantities, unrelated prices, unconfigured add-on price, and operator base-cap changes must always yield the configured base + blocks.
- **Unit validation:** strings, floats, booleans, negatives, and over-max values must be rejected before any Stripe call, at checkout and at the endpoint.
- **Item lifecycle:** create vs modify vs delete-at-zero, zero with no item as a no-op, and a stale/empty stored item id falling back to retrieval.
- **Partial effects:** a Stripe failure must leave local state unchanged; checkout must not create a customer for invalid units.
- **Existing plans:** add-on quantities above a lowered request cap still count; frozen/archived lifecycle behavior is unchanged.

---

### Task 1: Quota math, add-on item field, and webhook quota sync

**Files:**
- Create: `billing/objects.py`
- Create: `billing/quota.py`
- Modify: `billing/webhooks.py`
- Modify: `billing/models.py`
- Create: `billing/migrations/0004_addon_item.py` (generated)
- Modify: `config/settings/base.py`
- Create: `tests/test_billing_addons.py`

**Interfaces:**
- Consumes: `ComicPlan`, `settings.MEDIA_STORAGE_QUOTA_BYTES`, `settings.BILLING_STORAGE_ADDON_*`.
- Produces:
  - `billing.objects.get_field(obj, key, default=None)`.
  - `billing.quota.addon_units(subscription)` → `int`; `addon_item(subscription)` → `(str, int)`; `quota_bytes_for(subscription)` → `int`.
  - `ComicPlan.stripe_addon_item_id` (`str`, blank default).
  - Settings `BILLING_STORAGE_ADDON_PRICE`, `BILLING_STORAGE_ADDON_BYTES`, `BILLING_STORAGE_ADDON_MAX_UNITS`.

- [ ] **Step 1: Write the failing quota tests**

Create `tests/test_billing_addons.py`:

```python
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
    assert _post(client, "customer.subscription.created", subscription).status_code == 200
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_billing_addons.py -q`
Expected: `ImportError: cannot import name 'quota'` (or the missing `stripe_addon_item_id` field).

- [ ] **Step 3: Add the field and settings**

In `billing/models.py`, add to `ComicPlan` after `stripe_subscription_id`:

```python
    stripe_addon_item_id = models.CharField(max_length=255, blank=True, default="")
```

In `config/settings/base.py`, after `BILLING_ARCHIVE_AFTER_DAYS`:

```python
BILLING_STORAGE_ADDON_PRICE = env("BILLING_STORAGE_ADDON_PRICE", default="")
BILLING_STORAGE_ADDON_BYTES = env.int(
    "BILLING_STORAGE_ADDON_BYTES", default=10 * 1024**3
)
BILLING_STORAGE_ADDON_MAX_UNITS = env.int(
    "BILLING_STORAGE_ADDON_MAX_UNITS", default=100
)
```

Run: `uv run python manage.py makemigrations billing`
Expected: creates `billing/migrations/0004_addon_item.py` (rename the generated file if needed).

- [ ] **Step 4: Add the object accessor and quota module**

Create `billing/objects.py`:

```python
def get_field(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except (KeyError, TypeError):
        return default
```

Create `billing/quota.py`:

```python
from django.conf import settings

from billing.objects import get_field


def _items(subscription):
    items = get_field(subscription, "items") or {}
    return get_field(items, "data") or []


def _quantity(item):
    try:
        return int(get_field(item, "quantity") or 0)
    except (TypeError, ValueError):
        return 0


def addon_item(subscription):
    price_id = settings.BILLING_STORAGE_ADDON_PRICE
    if not price_id:
        return "", 0
    item_id = ""
    units = 0
    for item in _items(subscription):
        price = get_field(item, "price") or {}
        if get_field(price, "id") != price_id:
            continue
        item_id = item_id or get_field(item, "id") or ""
        units += _quantity(item)
    return item_id, units


def addon_units(subscription):
    return addon_item(subscription)[1]


def quota_bytes_for(subscription):
    return (
        settings.MEDIA_STORAGE_QUOTA_BYTES
        + addon_units(subscription) * settings.BILLING_STORAGE_ADDON_BYTES
    )
```

- [ ] **Step 5: Sync quota in the webhook**

In `billing/webhooks.py`, replace the local `_get` definition with an import so
there is one accessor:

```python
from billing import lifecycle, quota
from billing.objects import get_field

_get = get_field
```

In `sync_subscription`, before `get_or_create`, compute:

```python
    addon_item_id, _ = quota.addon_item(subscription)
    plan_quota = quota.quota_bytes_for(subscription)
```

Use them in `defaults`:

```python
        defaults={
            "quota_bytes": plan_quota,
            "stripe_addon_item_id": addon_item_id,
            "stripe_customer_id": customer or "",
            "stripe_subscription_id": subscription_id or "",
            "current_period_end": period_end,
        },
```

and in the existing-plan branch:

```python
        plan.quota_bytes = plan_quota
        plan.stripe_addon_item_id = addon_item_id
        plan.save(
            update_fields=[
                "quota_bytes",
                "stripe_addon_item_id",
                "stripe_customer_id",
                "stripe_subscription_id",
                "current_period_end",
            ]
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_billing_addons.py -q`
Expected: `7 passed`

- [ ] **Step 7: Lint, migration check, and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
uv run python manage.py makemigrations --check --dry-run
git add billing/objects.py billing/quota.py billing/webhooks.py billing/models.py billing/migrations/0004_addon_item.py config/settings/base.py tests/test_billing_addons.py
git commit -m "feat: derive plan quota from storage add-on items"
```

---

### Task 2: Checkout add-on units

**Files:**
- Modify: `billing/services.py`
- Modify: `billing/views.py`
- Modify: `tests/test_billing_addons.py`

**Interfaces:**
- Consumes: `settings.BILLING_STORAGE_ADDON_PRICE`, `BILLING_STORAGE_ADDON_MAX_UNITS`.
- Produces: `services.InvalidUnitsError`, `services.AddonsUnavailableError`, `services.validate_units(units)` → `int`; `start_checkout(comic, user, interval="month", addon_units=0)`; view error codes `invalid_units` (400) and `addons_unavailable` (409).

- [ ] **Step 1: Add failing checkout tests**

Add `import responses` and `from urllib.parse import parse_qs` to the top
imports of `tests/test_billing_addons.py`, then append:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_billing_addons.py -q -k "checkout"`
Expected: line-item and validation failures (current checkout ignores `addon_units`).

- [ ] **Step 3: Implement validation and line items**

In `billing/services.py`, add the errors:

```python
class InvalidUnitsError(Exception):
    pass


class AddonsUnavailableError(Exception):
    pass


class NoSubscriptionError(Exception):
    pass
```

Add the validator:

```python
def validate_units(units):
    if isinstance(units, bool) or not isinstance(units, int):
        raise InvalidUnitsError
    if units < 0 or units > settings.BILLING_STORAGE_ADDON_MAX_UNITS:
        raise InvalidUnitsError
    return units
```

Change `start_checkout` to accept units and build line items:

```python
def start_checkout(comic, user, interval="month", addon_units=0):
    if not settings.BILLING_ENABLED:
        raise BillingDisabledError
    if not is_owner(comic, user):
        raise NotOwnerError
    addon_units = validate_units(addon_units)
    if addon_units and not settings.BILLING_STORAGE_ADDON_PRICE:
        raise AddonsUnavailableError
    if ComicPlan.objects.filter(comic=comic, status=PlanStatus.ACTIVE).exists():
        raise AlreadySubscribedError
    price = price_for(interval)
    line_items = [{"price": price, "quantity": 1}]
    if addon_units:
        line_items.append(
            {"price": settings.BILLING_STORAGE_ADDON_PRICE, "quantity": addon_units}
        )
```

and pass `line_items=line_items` instead of the inline list. Keep the rest
(customer reuse, metadata, URLs, error wrapping) unchanged.

- [ ] **Step 4: Parse the body once in the view**

In `billing/views.py`, replace `_interval` with:

```python
def _payload(request):
    content_type = request.content_type or ""
    if not request.body.strip() or not content_type.startswith("application/json"):
        return {}
    try:
        payload = json.loads(request.body)
    except ValueError as exc:
        raise services.InvalidPayloadError from exc
    if not isinstance(payload, dict):
        raise services.InvalidPayloadError
    return payload
```

In `checkout`, use it and map the new errors:

```python
    try:
        payload = _payload(request)
        url = services.start_checkout(
            comic,
            request.user,
            payload.get("interval") or "month",
            payload.get("addon_units", 0),
        )
    except services.InvalidPayloadError:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    except services.InvalidUnitsError:
        return JsonResponse({"error": "invalid_units"}, status=400)
    except services.InvalidIntervalError:
        return JsonResponse({"error": "invalid_interval"}, status=400)
    except services.AddonsUnavailableError:
        return JsonResponse({"error": "addons_unavailable"}, status=409)
    except services.NotOwnerError:
        return JsonResponse({"error": "not_owner"}, status=403)
    except services.AlreadySubscribedError:
        return JsonResponse({"error": "already_subscribed"}, status=409)
    except services.StripeError:
        return JsonResponse({"error": "stripe_error"}, status=502)
    return JsonResponse({"checkout_url": url})
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_billing_addons.py -q`
Expected: all checkout and quota tests pass (15 tests).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add billing/services.py billing/views.py tests/test_billing_addons.py
git commit -m "feat: buy storage blocks at checkout"
```

---

### Task 3: Add-on management endpoint

**Files:**
- Modify: `billing/stripe_client.py`
- Modify: `billing/services.py`
- Modify: `billing/views.py`
- Modify: `billing/urls.py`
- Modify: `tests/test_billing_addons.py`

**Interfaces:**
- Consumes: `quota.addon_item`, `validate_units`, `InvalidUnitsError`, `AddonsUnavailableError`, `NoSubscriptionError`.
- Produces:
  - `stripe_client.retrieve_subscription(subscription_id)`,
    `create_subscription_item(**kwargs)`,
    `modify_subscription_item(item_id, **kwargs)`,
    `delete_subscription_item(item_id)`.
  - `services.set_addon_units(comic, user, units)` → `int`.
  - `views.addons`; URL name `billing-addons`, path
    `/comics/<uuid:comic_id>/billing/addons`.

- [ ] **Step 1: Add failing endpoint tests**

Add `PlanStatus` to the existing `from billing.models import ComicPlan` line
at the top of `tests/test_billing_addons.py`, then append:

```python
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
def test_addons_zero_deletes_item(client):
    owner, comic = _scene()
    _subscribed_plan(comic, stripe_addon_item_id="si_add")
    client.force_login(owner)
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
    assert len(responses.calls) == 1


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
                "data": [{"id": "si_found", "price": {"id": ADDON_PRICE}, "quantity": 1}]
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
    from comics.models import ComicRole

    ComicRole.objects.create(
        comic=comic, user=collaborator, role=ComicRole.Role.EDITOR
    )
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_billing_addons.py -q -k "addons"`
Expected: `404 != 200` (route missing) for the success paths.

- [ ] **Step 3: Add the Stripe calls**

Append to `billing/stripe_client.py`:

```python
def retrieve_subscription(subscription_id):
    return _run(stripe.Subscription.retrieve, subscription_id)


def create_subscription_item(**kwargs):
    return _run(stripe.SubscriptionItem.create, **kwargs)


def modify_subscription_item(item_id, **kwargs):
    return _run(stripe.SubscriptionItem.modify, item_id, **kwargs)


def delete_subscription_item(item_id):
    return _run(stripe.SubscriptionItem.delete, item_id)
```

- [ ] **Step 4: Implement the service**

Append to `billing/services.py` (and import `quota`):

```python
from billing import quota, stripe_client
```

```python
def set_addon_units(comic, user, units):
    if not settings.BILLING_ENABLED:
        raise BillingDisabledError
    if not is_owner(comic, user):
        raise NotOwnerError
    units = validate_units(units)
    price = settings.BILLING_STORAGE_ADDON_PRICE
    if not price:
        raise AddonsUnavailableError
    plan = ComicPlan.objects.filter(comic=comic).first()
    if (
        plan is None
        or not plan.stripe_subscription_id
        or plan.status not in (PlanStatus.ACTIVE, PlanStatus.PAST_DUE)
    ):
        raise NoSubscriptionError
    item_id = plan.stripe_addon_item_id
    try:
        if not item_id:
            subscription = stripe_client.retrieve_subscription(
                plan.stripe_subscription_id
            )
            item_id, _ = quota.addon_item(subscription)
            if item_id:
                plan.stripe_addon_item_id = item_id
                plan.save(update_fields=["stripe_addon_item_id"])
        if units == 0:
            if item_id:
                stripe_client.delete_subscription_item(item_id)
        elif item_id:
            stripe_client.modify_subscription_item(item_id, quantity=units)
        else:
            stripe_client.create_subscription_item(
                subscription=plan.stripe_subscription_id,
                price=price,
                quantity=units,
            )
    except stripe_client.StripeRequestError as exc:
        raise StripeError(str(exc)) from exc
    return units
```

- [ ] **Step 5: Implement the view and URL**

Append to `billing/views.py`:

```python
@billing_enabled
@require_POST
@login_required
def addons(request, comic_id):
    comic = get_object_or_404(Comic, pk=comic_id)
    try:
        payload = _payload(request)
        if "units" not in payload:
            raise services.InvalidUnitsError
        units = services.set_addon_units(comic, request.user, payload["units"])
    except services.InvalidPayloadError:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    except services.InvalidUnitsError:
        return JsonResponse({"error": "invalid_units"}, status=400)
    except services.NotOwnerError:
        return JsonResponse({"error": "not_owner"}, status=403)
    except services.AddonsUnavailableError:
        return JsonResponse({"error": "addons_unavailable"}, status=409)
    except services.NoSubscriptionError:
        return JsonResponse({"error": "no_subscription"}, status=409)
    except services.StripeError:
        return JsonResponse({"error": "stripe_error"}, status=502)
    return JsonResponse({"units": units})
```

Add to `billing/urls.py` inside `urlpatterns`:

```python
    path(
        "comics/<uuid:comic_id>/billing/addons",
        views.addons,
        name="billing-addons",
    ),
```

- [ ] **Step 6: Run the full add-on test file**

Run: `uv run pytest tests/test_billing_addons.py -q`
Expected: all pass (31 tests).

- [ ] **Step 7: Run the full suite, lint, and migration check**

Run: `uv run pytest -q`
Expected: previous total + new tests, no failures.

Run: `uv run ruff check .` and `uv run ruff format --check .`
Expected: clean.

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`

- [ ] **Step 8: Commit**

```bash
git add billing/stripe_client.py billing/services.py billing/views.py billing/urls.py tests/test_billing_addons.py
git commit -m "feat: manage storage add-on quantity"
```
