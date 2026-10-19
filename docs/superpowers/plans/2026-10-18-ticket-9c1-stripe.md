# Stripe Billing (9c1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a comic owner subscribe to the per-comic plan through Stripe Checkout, manage it through the Customer Portal, and keep `ComicPlan` in sync from signed Stripe webhooks.

**Architecture:** `billing` gains a `BillingCustomer` mapping (one Stripe customer per owner), a `stripe_client` seam that owns the SDK, `services` for checkout/portal domain logic with typed errors, and a `webhooks` module that maps subscription status onto `ComicPlan`. HTML views stay thin JSON handlers included from `config/urls.py`.

**Tech Stack:** Python 3.14, Django 6.1.1, `stripe` Python SDK (version resolved by `uv add`), pytest/pytest-django, `responses` to intercept the SDK's HTTP calls.

**Spec:** `docs/superpowers/specs/2026-10-18-stripe-billing-design.md`

## Global Constraints

- Python 3.14, Django 6.1.1; run everything through `uv run`.
- Do not guess the `stripe` version: add it with `uv add stripe` and let uv resolve and pin it.
- Settings are env-driven through `django-environ`; never hardcode a Stripe key, price id, or quota.
- `billing/stripe_client.py` is the only module that imports `stripe`.
- JSON error bodies are exactly `{"error": "<code>"}`; user-facing codes are `not_owner`, `invalid_payload`, `invalid_interval`, `already_subscribed`, `no_billing_account`, `stripe_error`, `invalid_signature`.
- `BILLING_ENABLED=False` (the default) makes every billing URL 404 and performs no Stripe calls.
- Quota defaults come from `settings.MEDIA_STORAGE_QUOTA_BYTES`; a plan's `quota_bytes` is written only when the plan row is created.
- Tests use `pytest.mark.django_db`, `override_settings`, and `responses`; sign webhooks with `hmac`/`time`, not SDK internals.
- Keep `ruff check .` and `ruff format --check .` clean; run `uv run python manage.py makemigrations --check --dry-run` before final commits.

## Review Focus

- **Stripe API version drift:** newer Stripe API versions moved the billing period onto subscription items, so `current_period_end` may be absent at the top level; sync must read either shape.
- **Out-of-order and replayed webhooks:** the same event or an older subscription update must be harmless; plan sync is last-write-wins and re-delivery must not create duplicate plans.
- **Stripe/network failure mid-checkout:** a 500 or auth failure must return 502 and leave no half-created `BillingCustomer` or `ComicPlan`.
- **Misconfigured or disabled self-host:** billing off must 404 without touching the SDK; missing webhook secret must 400, not crash.
- **Authorization boundaries:** editors/contributors/moderators and anonymous users must not start checkout or open the portal, and an active plan must not check out twice.

---

### Task 1: Billing settings and the owner→customer mapping

**Files:**
- Modify: `billing/models.py`
- Modify: `config/settings/base.py` (append billing settings after the `MEDIA_*` block, around line 154)
- Create: `billing/migrations/0002_billingcustomer.py` (generated)
- Create: `tests/test_billing.py`

**Interfaces:**
- Consumes: `core.models.UUIDModel`, `AUTH_USER_MODEL = accounts.User`, `INSTANCE_URL`, `MEDIA_STORAGE_QUOTA_BYTES`.
- Produces: `billing.models.BillingCustomer` with `user` (OneToOne, `related_name="billing_customer"`), `stripe_customer_id` (unique), `created_at`; settings `BILLING_ENABLED`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `BILLING_PRICE_MONTHLY`, `BILLING_PRICE_ANNUAL`, `BILLING_SUCCESS_URL`, `BILLING_CANCEL_URL`, `BILLING_PORTAL_RETURN_URL`.

- [ ] **Step 1: Write the failing model test**

Create `tests/test_billing.py`:

```python
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from billing.models import BillingCustomer


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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_billing.py -q`
Expected: `ImportError: cannot import name 'BillingCustomer'`

- [ ] **Step 3: Implement the model**

In `billing/models.py`, add the imports and model:

```python
from django.conf import settings
from django.db import models

from core.models import UUIDModel


class BillingCustomer(UUIDModel):
    """A Stripe customer owned by one user, shared by all their comics."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_customer",
    )
    stripe_customer_id = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} @ {self.stripe_customer_id}"
```

Keep the existing `PlanStatus` and `ComicPlan` definitions unchanged.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_billing.py -q`
Expected: `1 passed`

- [ ] **Step 5: Add the settings**

In `config/settings/base.py`, after `MEDIA_STORAGE_SECRET_KEY` (around line 153), add:

```python
BILLING_ENABLED = env.bool("BILLING_ENABLED", default=False)
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")
BILLING_PRICE_MONTHLY = env("BILLING_PRICE_MONTHLY", default="")
BILLING_PRICE_ANNUAL = env("BILLING_PRICE_ANNUAL", default="")
BILLING_SUCCESS_URL = env("BILLING_SUCCESS_URL", default=f"{INSTANCE_URL}/")
BILLING_CANCEL_URL = env("BILLING_CANCEL_URL", default=f"{INSTANCE_URL}/")
BILLING_PORTAL_RETURN_URL = env("BILLING_PORTAL_RETURN_URL", default=f"{INSTANCE_URL}/")
```

- [ ] **Step 6: Generate and verify the migration**

Run: `uv run python manage.py makemigrations billing`
Expected: creates `billing/migrations/0002_billingcustomer.py`

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`

- [ ] **Step 7: Commit**

```bash
git add billing/models.py billing/migrations/0002_billingcustomer.py config/settings/base.py tests/test_billing.py
git commit -m "feat: add billing customer mapping and Stripe settings"
```

---

### Task 2: Checkout — Stripe client, services, and endpoint

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add stripe`)
- Create: `billing/stripe_client.py`
- Create: `billing/services.py`
- Create: `billing/views.py`
- Create: `billing/urls.py`
- Modify: `config/urls.py`
- Modify: `tests/test_billing.py`

**Interfaces:**
- Consumes: `BillingCustomer`, `ComicPlan`, `PlanStatus` from Task 1; `comics.models.Comic`, `comics.models.ComicRole`; settings from Task 1.
- Produces:
  - `billing.stripe_client.StripeRequestError`
  - `billing.stripe_client.create_customer(user)` → object with `.id`
  - `billing.stripe_client.create_checkout_session(**kwargs)` → object with `.url`
  - `billing.services.BillingDisabled`, `NotOwner`, `AlreadySubscribed`, `InvalidInterval`, `InvalidPayload`, `NoBillingAccount`, `StripeError`
  - `billing.services.get_or_create_customer(user)` → `BillingCustomer`
  - `billing.services.start_checkout(comic, user, interval="month")` → `str` checkout URL
  - `billing.views.checkout(request, comic_id)` → JSON `{"checkout_url": url}`
  - URL name `billing-checkout`, path `/comics/<uuid:comic_id>/billing/checkout`

- [ ] **Step 1: Add the dependency**

Run: `uv add stripe`
Expected: `pyproject.toml` gains a pinned `stripe==<resolved>` and `uv.lock` updates. Do not hand-edit the version.

- [ ] **Step 2: Add the test helpers and checkout tests to `tests/test_billing.py`**

Replace the existing `from billing.models import BillingCustomer` line and add
the remaining imports:

```python
import json
from urllib.parse import parse_qs

import responses
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
```

Append these tests:

```python
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
    ComicRole.objects.create(
        comic=comic, user=collaborator, role=ComicRole.Role.EDITOR
    )
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
    ComicPlan.objects.create(
        comic=comic, status=PlanStatus.ACTIVE, quota_bytes=123
    )
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
```

- [ ] **Step 3: Run the checkout tests to verify they fail**

Run: `uv run pytest tests/test_billing.py -q`
Expected: failures, the first being `404 != 200` for `/comics/<id>/billing/checkout`.

- [ ] **Step 4: Implement the Stripe client seam**

Create `billing/stripe_client.py`:

```python
import logging

import stripe
from django.conf import settings

logger = logging.getLogger(__name__)


class StripeRequestError(Exception):
    pass


def _run(call, **kwargs):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        return call(**kwargs)
    except stripe.StripeError as exc:
        logger.warning("Stripe request failed: %s", exc)
        raise StripeRequestError(str(exc)) from exc


def create_customer(user):
    return _run(
        stripe.Customer.create,
        email=user.email,
        metadata={"user_id": str(user.id)},
    )


def create_checkout_session(**kwargs):
    return _run(stripe.checkout.Session.create, **kwargs)
```

- [ ] **Step 5: Implement the services**

Create `billing/services.py`:

```python
from django.conf import settings

from billing import stripe_client
from billing.models import BillingCustomer, ComicPlan, PlanStatus
from comics.models import ComicRole


class BillingDisabled(Exception):
    pass


class NotOwner(Exception):
    pass


class AlreadySubscribed(Exception):
    pass


class InvalidInterval(Exception):
    pass


class InvalidPayload(Exception):
    pass


class NoBillingAccount(Exception):
    pass


class StripeError(Exception):
    pass


def is_owner(comic, user):
    return comic.roles.filter(user=user, role=ComicRole.Role.OWNER).exists()


def get_or_create_customer(user):
    customer = stripe_client.create_customer(user)
    account, _ = BillingCustomer.objects.get_or_create(user=user)
    account.stripe_customer_id = customer.id
    account.save(update_fields=["stripe_customer_id"])
    return account


def price_for(interval):
    if interval == "month":
        price = settings.BILLING_PRICE_MONTHLY
    elif interval == "year":
        price = settings.BILLING_PRICE_ANNUAL
    else:
        raise InvalidInterval(interval)
    if not price:
        if interval == "year":
            raise InvalidInterval(interval)
        raise StripeError("missing monthly price")
    return price


def start_checkout(comic, user, interval="month"):
    if not settings.BILLING_ENABLED:
        raise BillingDisabled
    if not is_owner(comic, user):
        raise NotOwner
    if ComicPlan.objects.filter(comic=comic, status=PlanStatus.ACTIVE).exists():
        raise AlreadySubscribed
    price = price_for(interval)
    existing = BillingCustomer.objects.filter(user=user).first()
    if existing is not None and existing.stripe_customer_id:
        account = existing
    else:
        try:
            account = get_or_create_customer(user)
        except stripe_client.StripeRequestError as exc:
            raise StripeError(str(exc)) from exc
    try:
        session = stripe_client.create_checkout_session(
            mode="subscription",
            customer=account.stripe_customer_id,
            line_items=[{"price": price, "quantity": 1}],
            success_url=settings.BILLING_SUCCESS_URL,
            cancel_url=settings.BILLING_CANCEL_URL,
            client_reference_id=str(comic.id),
            subscription_data={
                "metadata": {"comic_id": str(comic.id), "user_id": str(user.id)}
            },
        )
    except stripe_client.StripeRequestError as exc:
        raise StripeError(str(exc)) from exc
    return session.url
```

- [ ] **Step 6: Implement the views and URLs**

Create `billing/views.py`:

```python
import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from billing import services
from comics.models import Comic


def _require_enabled():
    if not settings.BILLING_ENABLED:
        raise Http404


def _interval(request):
    if not request.body:
        return "month"
    try:
        payload = json.loads(request.body)
    except ValueError as exc:
        raise services.InvalidPayload from exc
    if not isinstance(payload, dict):
        raise services.InvalidPayload
    return payload.get("interval") or "month"


@require_POST
@login_required
def checkout(request, comic_id):
    _require_enabled()
    comic = get_object_or_404(Comic, pk=comic_id)
    try:
        url = services.start_checkout(comic, request.user, _interval(request))
    except services.InvalidPayload:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    except services.InvalidInterval:
        return JsonResponse({"error": "invalid_interval"}, status=400)
    except services.NotOwner:
        return JsonResponse({"error": "not_owner"}, status=403)
    except services.AlreadySubscribed:
        return JsonResponse({"error": "already_subscribed"}, status=409)
    except services.StripeError:
        return JsonResponse({"error": "stripe_error"}, status=502)
    return JsonResponse({"checkout_url": url})
```

Create `billing/urls.py`:

```python
from django.urls import path

from billing import views

urlpatterns = [
    path(
        "comics/<uuid:comic_id>/billing/checkout",
        views.checkout,
        name="billing-checkout",
    ),
]
```

Modify `config/urls.py`: add `path("", include("billing.urls")),` after the `memberships.urls` include.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_billing.py -q`
Expected: all checkout tests pass (11 tests total so far).

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add pyproject.toml uv.lock billing/stripe_client.py billing/services.py billing/views.py billing/urls.py config/urls.py tests/test_billing.py
git commit -m "feat: start Stripe Checkout from the owner endpoint"
```

---

### Task 3: Customer Portal endpoint

**Files:**
- Modify: `billing/stripe_client.py`
- Modify: `billing/services.py`
- Modify: `billing/views.py`
- Modify: `billing/urls.py`
- Modify: `tests/test_billing.py`

**Interfaces:**
- Consumes: `BillingCustomer`, `is_owner`, `NoBillingAccount`, `StripeError`, `StripeRequestError` from Task 2.
- Produces: `billing.stripe_client.create_portal_session(**kwargs)` → object with `.url`; `billing.services.open_portal(comic, user)` → `str`; `billing.views.portal`; URL name `billing-portal`, path `/comics/<uuid:comic_id>/billing/portal`.

- [ ] **Step 1: Add the failing portal tests to `tests/test_billing.py`**

```python
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
        json={"id": "bps_1", "object": "billing_portal.session", "url": "https://billing.stripe.test/bps_1"},
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
```

- [ ] **Step 2: Run the portal tests to verify they fail**

Run: `uv run pytest tests/test_billing.py -q -k portal`
Expected: `404 != 200` (route missing).

- [ ] **Step 3: Implement the client call**

Append to `billing/stripe_client.py`:

```python
def create_portal_session(**kwargs):
    return _run(stripe.billing_portal.Session.create, **kwargs)
```

- [ ] **Step 4: Implement the service**

Append to `billing/services.py`:

```python
def open_portal(comic, user):
    if not settings.BILLING_ENABLED:
        raise BillingDisabled
    if not is_owner(comic, user):
        raise NotOwner
    account = BillingCustomer.objects.filter(user=user).first()
    if account is None or not account.stripe_customer_id:
        raise NoBillingAccount
    try:
        session = stripe_client.create_portal_session(
            customer=account.stripe_customer_id,
            return_url=settings.BILLING_PORTAL_RETURN_URL,
        )
    except stripe_client.StripeRequestError as exc:
        raise StripeError(str(exc)) from exc
    return session.url
```

- [ ] **Step 5: Implement the view and URL**

Append to `billing/views.py`:

```python
@require_POST
@login_required
def portal(request, comic_id):
    _require_enabled()
    comic = get_object_or_404(Comic, pk=comic_id)
    try:
        url = services.open_portal(comic, request.user)
    except services.NotOwner:
        return JsonResponse({"error": "not_owner"}, status=403)
    except services.NoBillingAccount:
        return JsonResponse({"error": "no_billing_account"}, status=409)
    except services.StripeError:
        return JsonResponse({"error": "stripe_error"}, status=502)
    return JsonResponse({"portal_url": url})
```

Append to `billing/urls.py` inside `urlpatterns`:

```python
    path(
        "comics/<uuid:comic_id>/billing/portal",
        views.portal,
        name="billing-portal",
    ),
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_billing.py -q`
Expected: all tests pass (16 tests total so far).

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add billing/stripe_client.py billing/services.py billing/views.py billing/urls.py tests/test_billing.py
git commit -m "feat: open the Stripe Customer Portal for owners"
```

---

### Task 4: Signed webhook and plan sync

**Files:**
- Modify: `billing/stripe_client.py`
- Create: `billing/webhooks.py`
- Modify: `billing/views.py`
- Modify: `billing/urls.py`
- Modify: `tests/test_billing.py`

**Interfaces:**
- Consumes: `ComicPlan`, `PlanStatus`, `Comic`, settings.
- Produces:
  - `billing.stripe_client.InvalidSignature`
  - `billing.stripe_client.construct_event(payload, signature, secret)` → Stripe `Event`
  - `billing.webhooks.SUBSCRIPTION_EVENTS` (set of the three event type strings)
  - `billing.webhooks.plan_status_for(status)` → `PlanStatus`
  - `billing.webhooks.sync_subscription(subscription)` → `ComicPlan | None`
  - `billing.views.webhook`; URL name `billing-webhook`, path `/billing/webhook`

- [ ] **Step 1: Add the failing webhook tests to `tests/test_billing.py`**

Add the stdlib imports at the top and add `webhooks` to the existing billing
imports (the `ComicPlan, PlanStatus` import already exists from Task 2):

```python
import hashlib
import hmac
import time
from datetime import UTC, datetime

from django.conf import settings

from billing import webhooks
```

Add the helpers and tests:

```python
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
    ComicPlan.objects.create(
        comic=comic, status=PlanStatus.ACTIVE, quota_bytes=12_345
    )
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
    assert (
        _post_event(client, "invoice.paid", comic).status_code == 200
    )
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
        _post_event(
            client, "customer.subscription.created", metadata={}
        ).status_code
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
```

- [ ] **Step 2: Run the webhook tests to verify they fail**

Run: `uv run pytest tests/test_billing.py -q -k webhook`
Expected: `404 != 200` (route missing) and `ImportError: cannot import name 'webhooks'` for the mapping test.

- [ ] **Step 3: Add signature verification to the client seam**

Append to `billing/stripe_client.py`:

```python
class InvalidSignature(Exception):
    pass


def construct_event(payload, signature, secret):
    if not secret:
        raise InvalidSignature("missing webhook secret")
    try:
        return stripe.Webhook.construct_event(payload, signature, secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise InvalidSignature(str(exc)) from exc
```

- [ ] **Step 4: Implement the plan sync**

Create `billing/webhooks.py`:

```python
from datetime import UTC, datetime
from uuid import UUID

from django.conf import settings

from billing.models import ComicPlan, PlanStatus
from comics.models import Comic

SUBSCRIPTION_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}

ACTIVE_STATUSES = {"active", "trialing"}


def plan_status_for(status):
    if str(status or "").lower() in ACTIVE_STATUSES:
        return PlanStatus.ACTIVE
    return PlanStatus.PAST_DUE


def _period_end(subscription):
    raw = subscription.get("current_period_end")
    if raw is None:
        items = subscription.get("items") or {}
        for item in items.get("data") or []:
            if item.get("current_period_end") is not None:
                raw = item["current_period_end"]
                break
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def sync_subscription(subscription):
    metadata = subscription.get("metadata") or {}
    try:
        comic_id = UUID(str(metadata.get("comic_id")))
    except (TypeError, ValueError):
        return None
    comic = Comic.objects.filter(pk=comic_id).first()
    if comic is None:
        return None
    customer = subscription.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    subscription_id = subscription.get("id")
    period_end = _period_end(subscription)
    plan = ComicPlan.objects.filter(comic=comic).first()
    if plan is None:
        return ComicPlan.objects.create(
            comic=comic,
            status=plan_status_for(subscription.get("status")),
            quota_bytes=settings.MEDIA_STORAGE_QUOTA_BYTES,
            stripe_customer_id=customer or "",
            stripe_subscription_id=subscription_id or "",
            current_period_end=period_end,
        )
    plan.status = plan_status_for(subscription.get("status"))
    plan.stripe_customer_id = customer or plan.stripe_customer_id
    plan.stripe_subscription_id = subscription_id or plan.stripe_subscription_id
    if period_end is not None:
        plan.current_period_end = period_end
    plan.save(
        update_fields=[
            "status",
            "stripe_customer_id",
            "stripe_subscription_id",
            "current_period_end",
        ]
    )
    return plan
```

- [ ] **Step 5: Implement the webhook view and URL**

Add `from django.views.decorators.csrf import csrf_exempt` and
`from billing import stripe_client, webhooks` to the top of `billing/views.py`,
then append the view:

```python


@csrf_exempt
@require_POST
def webhook(request):
    _require_enabled()
    try:
        event = stripe_client.construct_event(
            request.body,
            request.headers.get("Stripe-Signature", ""),
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except stripe_client.InvalidSignature:
        return JsonResponse({"error": "invalid_signature"}, status=400)
    if event["type"] in webhooks.SUBSCRIPTION_EVENTS:
        webhooks.sync_subscription(event["data"]["object"])
    return JsonResponse({"received": True})
```

Add `path("billing/webhook", views.webhook, name="billing-webhook"),` to `billing/urls.py`.

- [ ] **Step 6: Run the full billing test file**

Run: `uv run pytest tests/test_billing.py -q`
Expected: all tests pass (27 tests total).

- [ ] **Step 7: Run the full suite, lint, and migration check**

Run: `uv run pytest -q`
Expected: previous suite total + new tests, no failures.

Run: `uv run ruff check .` and `uv run ruff format --check .`
Expected: clean.

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`

- [ ] **Step 8: Commit**

```bash
git add billing/stripe_client.py billing/webhooks.py billing/views.py billing/urls.py tests/test_billing.py
git commit -m "feat: sync comic plans from signed Stripe webhooks"
```
