# Ticket 9b1 — Ko-fi Ingest & Linking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest Ko-fi webhook payments into pending supporter records, link them to a reader whose account email matches, and infer membership validity from renewals.

**Architecture:** `memberships.ProviderConnection` stores a per-comic, encrypted Ko-fi webhook token; `memberships.ProviderSupporter` holds off-platform subscriber state keyed by email, independent of `Actor`. `memberships/kofi.py` verifies the token, extends validity on subscription payments, refreshes already-linked statuses, and links a pending record to a user actor when their account email matches. A webhook view and an expiry sweep complete the loop.

**Tech Stack:** Django 6.1.1, PostgreSQL, `actors.crypto` (Fernet), pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §7 (Ko-fi webhook-only, `verification_token`, no cancel event → infer lapse from missed renewals; email→Actor linking; access = active status), §14 ticket 9.

## Global Constraints

- Synchronous Django only. Run Python via `uv run` (Python 3.14).
- Provider tokens/API keys are encrypted at rest with `actors.crypto` and compared in constant time; never log or echo them.
- A **subscription** payment sets `active` and `valid_until = now + 30 days + 3-day grace`, extending on every renewal; a **donation** never grants or extends membership.
- 9b1 links a pending record only when the reader's **account email** matches the provider email (case-insensitive). Token-based email verification and remote-reader linking are 9b3.
- A linked status requires a `Tier` whose `name` matches the provider's `tier_name`; no match → link without a status.
- Only Ko-fi is wired; BMAC is 9b2.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- A bad/missing webhook token must store nothing. Task 2 (and view, Task 3).
- A one-off donation must not grant access. Task 2.
- A renewal must extend validity; an expiry sweep must deactivate lapsed supporters. Tasks 2, 3.
- Linking must reject a provider email that differs from the account email. Task 2.
- A tier name with no matching `Tier` must not create a status. Task 2.
- Stored tokens must not be plaintext. Task 1.

## File Structure

- `memberships/models.py` — add `ProviderConnection`, `ProviderSupporter`. (Task 1)
- `memberships/migrations/0002_*.py` — generated. (Task 1)
- `memberships/kofi.py` — new: `normalize_email`, `verify_token`, `process_kofi_event`, `attach_pending_supporter`. (Task 2)
- `memberships/views.py`, `memberships/urls.py`, `config/urls.py` — Ko-fi webhook. (Task 3)
- `memberships/management/commands/expire_supporters.py` — sweep. (Task 3)
- Tests: `tests/test_kofi.py`.

---

### Task 1: Provider connection and pending supporter models

**Files:**
- Modify: `memberships/models.py`
- Create: `memberships/migrations/0002_*.py` (generated)
- Test: `tests/test_kofi.py`

**Interfaces:**
- Consumes: `actors.crypto.encrypt/decrypt`, `comics.models.Comic`, `core.models.UUIDModel`, `memberships.models.Provider`.
- Produces: `ProviderConnection` (`comic`, `provider`, encrypted `webhook_token`/`api_key`, `set_webhook_token`/`get_webhook_token`), `ProviderSupporter` (`connection`, `email`, `tier_name`, `active`, `valid_until`, `last_payment_at`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kofi.py`:

```python
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from comics.services import create_comic
from memberships.models import Provider, ProviderConnection, ProviderSupporter


def user(email=None):
    return get_user_model().objects.create_user(
        email=email or f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


def scene(token="secret"):
    owner = user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    connection = ProviderConnection(comic=comic, provider=Provider.KOFI)
    connection.set_webhook_token(token)
    connection.save()
    return owner, comic, connection


@pytest.mark.django_db
def test_webhook_token_is_encrypted_and_round_trips():
    _, _, connection = scene("super-secret")
    connection.refresh_from_db()
    raw = bytes(connection.webhook_token)
    assert b"super-secret" not in raw
    assert connection.get_webhook_token() == "super-secret"


@pytest.mark.django_db
def test_provider_connection_is_unique_per_comic_and_provider():
    _, comic, _ = scene()
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        ProviderConnection.objects.create(
            comic=comic, provider=Provider.KOFI, webhook_token=b"x"
        )


@pytest.mark.django_db
def test_provider_supporter_is_unique_per_connection_email():
    _, _, connection = scene()
    ProviderSupporter.objects.create(connection=connection, email="a@example.com")
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        ProviderSupporter.objects.create(
            connection=connection, email="a@example.com"
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_kofi.py -q`
Expected: ERROR — `ProviderConnection`/`ProviderSupporter` do not exist.

- [ ] **Step 3: Add the models**

Append to `memberships/models.py` (add `from actors import crypto` to the imports):

```python
class ProviderConnection(UUIDModel):
    comic = models.ForeignKey(
        "comics.Comic", on_delete=models.CASCADE, related_name="provider_connections"
    )
    provider = models.CharField(max_length=16, choices=Provider.choices)
    webhook_token = models.BinaryField(blank=True, default=b"")
    api_key = models.BinaryField(blank=True, default=b"")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "provider"], name="unique_provider_connection"
            )
        ]

    def set_webhook_token(self, token):
        self.webhook_token = crypto.encrypt(token.encode())

    def get_webhook_token(self):
        if not self.webhook_token:
            return ""
        return crypto.decrypt(bytes(self.webhook_token)).decode()

    def set_api_key(self, key):
        self.api_key = crypto.encrypt(key.encode())

    def get_api_key(self):
        if not self.api_key:
            return ""
        return crypto.decrypt(bytes(self.api_key)).decode()

    def __str__(self):
        return f"{self.comic}:{self.provider}"


class ProviderSupporter(UUIDModel):
    connection = models.ForeignKey(
        ProviderConnection, on_delete=models.CASCADE, related_name="supporters"
    )
    email = models.EmailField()
    tier_name = models.CharField(max_length=100, blank=True, default="")
    active = models.BooleanField(default=False)
    valid_until = models.DateTimeField(null=True, blank=True)
    last_payment_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "email"], name="unique_provider_supporter"
            )
        ]
        ordering = ["connection", "email"]

    def __str__(self):
        return f"{self.email} @ {self.connection}"
```

- [ ] **Step 4: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations memberships
uv run python manage.py migrate
uv run pytest tests/test_kofi.py -q
```
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add memberships/models.py memberships/migrations tests/test_kofi.py
git commit -m "feat: Ko-fi provider connection and pending supporter models"
```

---

### Task 2: Ko-fi ingest, renewal validity, and account-email linking

**Files:**
- Create: `memberships/kofi.py`
- Test: `tests/test_kofi.py`

**Interfaces:**
- Consumes: Task 1 models; `memberships.services.link_supporter`; `memberships.models.{SupporterStatus, Tier}`.
- Produces: `memberships.kofi.{normalize_email, verify_token, process_kofi_event, attach_pending_supporter}`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_kofi.py` (add imports: `from datetime import timedelta`, `from django.utils import timezone`, `from actors.services import create_local_actor`, `from memberships.kofi import attach_pending_supporter, process_kofi_event, verify_token`, `from memberships.models import SupporterLink, SupporterStatus, Tier`, `from memberships.services import supporter_has_access`):

```python
def kofi_payload(
    email="reader@example.com",
    token="secret",
    subscription=True,
    tier_name="Fan",
):
    return {
        "verification_token": token,
        "email": email,
        "is_subscription_payment": subscription,
        "tier_name": tier_name,
        "type": "Subscription" if subscription else "Donation",
        "amount": "5.00",
        "kofi_transaction_id": uuid4().hex,
    }


@pytest.mark.django_db
def test_verify_token():
    _, _, connection = scene()
    assert verify_token(connection, "secret") is True
    assert verify_token(connection, "wrong") is False


@pytest.mark.django_db
def test_subscription_creates_active_supporter():
    _, _, connection = scene()
    before = timezone.now()
    supporter = process_kofi_event(connection, kofi_payload())
    assert supporter.active is True
    assert supporter.tier_name == "Fan"
    assert supporter.valid_until >= before + timedelta(days=33)
    assert supporter.last_payment_at is not None


@pytest.mark.django_db
def test_donation_does_not_grant_membership():
    _, _, connection = scene()
    supporter = process_kofi_event(connection, kofi_payload(subscription=False))
    assert supporter.active is False
    assert supporter.valid_until is None


@pytest.mark.django_db
def test_bad_token_stores_nothing():
    _, _, connection = scene()
    assert process_kofi_event(connection, kofi_payload(token="wrong")) is None
    assert ProviderSupporter.objects.count() == 0


@pytest.mark.django_db
def test_renewal_extends_validity():
    _, _, connection = scene()
    supporter = process_kofi_event(connection, kofi_payload())
    supporter.valid_until = timezone.now() + timedelta(days=1)
    supporter.save()
    renewed = process_kofi_event(connection, kofi_payload())
    assert renewed.valid_until >= timezone.now() + timedelta(days=33)


@pytest.mark.django_db
def test_attach_pending_links_matching_account_email():
    _, comic, connection = scene()
    Tier.objects.create(comic=comic, name="Fan", rank=1)
    reader = user("Reader@Example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    process_kofi_event(
        connection, kofi_payload(email="reader@example.com", tier_name="Fan")
    )
    link = attach_pending_supporter(
        actor, comic, Provider.KOFI, "reader@example.com"
    )
    assert link is not None
    assert SupporterLink.objects.filter(comic=comic, actor=actor).exists()
    assert SupporterStatus.objects.filter(link=link, tier__name="Fan").exists()
    assert supporter_has_access(actor, comic) is True


@pytest.mark.django_db
def test_attach_rejects_email_mismatch():
    _, comic, connection = scene()
    Tier.objects.create(comic=comic, name="Fan", rank=1)
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    process_kofi_event(connection, kofi_payload(email="other@example.com"))
    assert (
        attach_pending_supporter(actor, comic, Provider.KOFI, "other@example.com")
        is None
    )
    assert not SupporterLink.objects.filter(actor=actor).exists()


@pytest.mark.django_db
def test_attach_without_tier_match_creates_no_status():
    _, comic, connection = scene()
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    process_kofi_event(connection, kofi_payload(tier_name="Unknown"))
    link = attach_pending_supporter(
        actor, comic, Provider.KOFI, "reader@example.com"
    )
    assert link is not None
    assert not SupporterStatus.objects.filter(link=link).exists()
    assert supporter_has_access(actor, comic) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_kofi.py -q -k "subscription or donation or token or renewal or attach"`
Expected: ERROR — `memberships.kofi` does not exist.

- [ ] **Step 3: Implement `memberships/kofi.py`**

```python
import hmac
from datetime import timedelta

from django.utils import timezone

from memberships.models import (
    ProviderConnection,
    ProviderSupporter,
    SupporterStatus,
    Tier,
)
from memberships.services import link_supporter

SUBSCRIPTION_PERIOD = timedelta(days=30)
GRACE_PERIOD = timedelta(days=3)


def normalize_email(email):
    return (email or "").strip().lower()


def verify_token(connection, token):
    stored = connection.get_webhook_token()
    if not stored or not token:
        return False
    return hmac.compare_digest(stored, str(token))


def _is_subscription(payload):
    value = payload.get("is_subscription_payment")
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes"}


def process_kofi_event(connection, payload):
    if not verify_token(connection, payload.get("verification_token")):
        return None
    email = normalize_email(payload.get("email"))
    if not email:
        return None
    supporter, _ = ProviderSupporter.objects.get_or_create(
        connection=connection, email=email
    )
    now = timezone.now()
    supporter.last_payment_at = now
    if _is_subscription(payload):
        supporter.tier_name = str(payload.get("tier_name") or "").strip()
        supporter.active = True
        supporter.valid_until = now + SUBSCRIPTION_PERIOD + GRACE_PERIOD
    supporter.save()
    _refresh_linked_status(supporter)
    return supporter


def _refresh_linked_status(supporter):
    if not supporter.active or supporter.valid_until is None:
        return
    comic = supporter.connection.comic
    link = comic.supporter_links.filter(
        provider=supporter.connection.provider,
        provider_email__iexact=supporter.email,
    ).first()
    if link is None:
        return
    tier = Tier.objects.filter(comic=comic, name=supporter.tier_name).first()
    if tier is None:
        return
    SupporterStatus.objects.create(
        link=link, tier=tier, valid_until=supporter.valid_until
    )


def attach_pending_supporter(actor, comic, provider, email):
    normalized = normalize_email(email)
    user_obj = getattr(actor, "user", None)
    if user_obj is None or user_obj.email.strip().lower() != normalized:
        return None
    link = link_supporter(actor, comic, provider, normalized)
    connection = ProviderConnection.objects.filter(
        comic=comic, provider=provider
    ).first()
    if connection is None:
        return link
    supporter = ProviderSupporter.objects.filter(
        connection=connection, email=normalized
    ).first()
    if supporter is not None and supporter.active and supporter.valid_until is not None:
        tier = Tier.objects.filter(comic=comic, name=supporter.tier_name).first()
        if tier is not None:
            SupporterStatus.objects.create(
                link=link, tier=tier, valid_until=supporter.valid_until
            )
    return link
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_kofi.py -q`
Expected: PASS (11 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add memberships/kofi.py tests/test_kofi.py
git commit -m "feat: Ko-fi ingest, renewal validity, and account-email linking"
```

---

### Task 3: Webhook endpoint and expiry sweep

**Files:**
- Create: `memberships/views.py`, `memberships/urls.py`, `memberships/management/__init__.py`, `memberships/management/commands/__init__.py`, `memberships/management/commands/expire_supporters.py`
- Modify: `config/urls.py`
- Test: `tests/test_kofi.py`

**Interfaces:**
- Consumes: Task 2 `process_kofi_event`; Task 1 `ProviderConnection`; Task 1 `ProviderSupporter`/`SupporterStatus`.
- Produces: `POST /webhooks/kofi/<uuid:comic_id>`; the `expire_supporters` command.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_kofi.py` (add imports: `from django.core.management import call_command`, `from memberships.models import SupporterStatus` [already], `from datetime import timedelta` [already]):

```python
@pytest.mark.django_db
def test_webhook_accepts_valid_json(client):
    _, comic, connection = scene()
    response = client.post(
        f"/webhooks/kofi/{comic.id}",
        data=kofi_payload(),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert ProviderSupporter.objects.filter(email="reader@example.com").exists()


@pytest.mark.django_db
def test_webhook_accepts_form_encoded(client):
    _, comic, connection = scene()
    response = client.post(f"/webhooks/kofi/{comic.id}", data=kofi_payload())
    assert response.status_code == 200
    assert ProviderSupporter.objects.count() == 1


@pytest.mark.django_db
def test_webhook_rejects_bad_token(client):
    _, comic, connection = scene()
    response = client.post(
        f"/webhooks/kofi/{comic.id}",
        data=kofi_payload(token="wrong"),
        content_type="application/json",
    )
    assert response.status_code == 403
    assert ProviderSupporter.objects.count() == 0


@pytest.mark.django_db
def test_webhook_404_for_unknown_comic_and_405_for_get(client):
    _, _, connection = scene()
    assert client.get(f"/webhooks/kofi/{connection.comic_id}").status_code == 405
    from uuid import uuid4 as new_uuid

    assert (
        client.post(
            f"/webhooks/kofi/{new_uuid()}",
            data=kofi_payload(),
            content_type="application/json",
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_expire_command_deactivates_lapsed_supporters():
    _, comic, connection = scene()
    past = timezone.now() - timedelta(days=1)
    supporter = ProviderSupporter.objects.create(
        connection=connection,
        email="reader@example.com",
        active=True,
        valid_until=past,
    )
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    link = SupporterLink.objects.create(
        comic=comic,
        actor=create_local_actor("lapsed"),
        provider=Provider.KOFI,
        provider_email="reader@example.com",
    )
    SupporterStatus.objects.create(link=link, tier=tier, active=True, valid_until=past)

    call_command("expire_supporters")

    supporter.refresh_from_db()
    assert supporter.active is False
    assert not SupporterStatus.objects.filter(active=True).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_kofi.py -q -k "webhook or expire"`
Expected: FAIL — no route/view/command.

- [ ] **Step 3: Implement the webhook view and routes**

Create `memberships/views.py`:

```python
import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from memberships import kofi
from memberships.models import Provider, ProviderConnection


@csrf_exempt
def kofi_webhook(request, comic_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    connection = get_object_or_404(
        ProviderConnection, comic_id=comic_id, provider=Provider.KOFI
    )
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"error": "invalid json"}, status=400)
    else:
        payload = request.POST.dict()
    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid payload"}, status=400)
    if kofi.process_kofi_event(connection, payload) is None:
        return JsonResponse({"error": "invalid token"}, status=403)
    return JsonResponse({"ok": True})
```

Create `memberships/urls.py`:

```python
from django.urls import path

from memberships import views

urlpatterns = [
    path(
        "webhooks/kofi/<uuid:comic_id>",
        views.kofi_webhook,
        name="kofi-webhook",
    ),
]
```

In `config/urls.py`, add `path("", include("memberships.urls"))`.

- [ ] **Step 4: Implement the expiry command**

Create empty `memberships/management/__init__.py` and `memberships/management/commands/__init__.py`.

Create `memberships/management/commands/expire_supporters.py`:

```python
from django.core.management.base import BaseCommand
from django.utils import timezone

from memberships.models import ProviderSupporter, SupporterStatus


class Command(BaseCommand):
    help = "Deactivate lapsed provider supporters and supporter statuses."

    def handle(self, *args, **options):
        now = timezone.now()
        supporters = ProviderSupporter.objects.filter(
            active=True, valid_until__lt=now
        ).update(active=False)
        statuses = SupporterStatus.objects.filter(
            active=True, valid_until__lt=now
        ).update(active=False)
        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {supporters} supporter(s), {statuses} status(es)."
            )
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_kofi.py -q`
Expected: PASS (16 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add memberships/views.py memberships/urls.py memberships/management config/urls.py tests/test_kofi.py
git commit -m "feat: Ko-fi webhook endpoint and supporter expiry sweep"
```

---

## Self-Review

**Spec coverage (Ticket 9b1 scope):** Ko-fi webhook-only ingest with token verification (§7); subscription renewals extend validity with a 3-day grace and inferred lapse (§7); pending off-platform supporter state keyed by email; account-email→Actor linking; access via the 9a gate. Out of scope by design: BMAC (9b2), token-based email verification + remote-reader linking (9b3), connect UI, donation→membership.

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `ProviderConnection`/`ProviderSupporter` are the single provider-state vocabulary; `process_kofi_event` is the single ingest path; `attach_pending_supporter` is the single linking path; `verify_token` is the single token check; `expire_supporters` is the single lapse sweep.

**Review Focus coverage:** bad token stores nothing — Tasks 2, 3; donation no access — Task 2; renewal extends + sweep — Tasks 2, 3; email mismatch rejected — Task 2; no tier → no status — Task 2; encrypted token — Task 1.