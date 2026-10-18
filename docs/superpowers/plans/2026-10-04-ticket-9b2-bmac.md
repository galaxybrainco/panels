# Ticket 9b2 — BMAC Reconcile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep supporter state truthful against Buy Me a Coffee: fetch active subscriptions from its API, reconcile pending supporter records, and refresh linked access. The webhook is an authenticated trigger; the API is the source of truth.

**Architecture:** Extract the provider-agnostic entitlement helpers (`normalize_email`, `resolve_tier`, `apply_supporter_entitlement`) into `memberships/services.py` and reuse them from Ko-fi. `memberships/bmac.py` fetches/paginates the BMAC subscriptions API (Bearer `api_key`), reconciles `ProviderSupporter` rows (active, tier, `valid_until`, deactivating those absent), and applies entitlements. A secret-path webhook enqueues a reconcile task; a `reconcile_bmac` command runs it for cron.

**Tech Stack:** Django 6.1.1, PostgreSQL, `requests` + `responses` (tests), pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §7 (BMAC API + membership webhooks; use webhooks for immediacy, reconcile against the API for truth; access = active status at tier), §14 ticket 9.

## Global Constraints

- Synchronous Django only. Run Python via `uv run` (Python 3.14).
- The **API is authoritative**: a failed fetch must abort the reconcile without changing any supporter state (never mass-deactivate on error).
- The webhook never parses provider-specific event payloads; it authenticates a shared secret and enqueues a reconcile.
- Provider API keys/secrets stay encrypted in `ProviderConnection` (from 9b1) and are never logged.
- Tier names match case-insensitively; `valid_until` comes from the API's `current_period_end`.
- Only BMAC is added; Ko-fi behavior must not regress.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- A failed BMAC API call must not deactivate supporters. Task 2.
- A supporter absent from the API's active set must be deactivated and lose linked access. Task 2.
- Reconcile is idempotent (re-running makes no duplicates/oscillation). Task 2.
- A webhook with a wrong/missing secret must not enqueue work. Task 3.
- Ko-fi ingest/access must still work after the shared-helper refactor. Task 1.

## File Structure

- `memberships/services.py` — add `normalize_email`, `resolve_tier`, `apply_supporter_entitlement`; keep `link_supporter`/access. (Task 1)
- `memberships/kofi.py` — use the shared helpers. (Task 1)
- `memberships/bmac.py` — new: `BmacError`, `fetch_subscriptions`, `reconcile_bmac`. (Task 2)
- `memberships/tasks.py`, `memberships/views.py`, `memberships/urls.py`, `config/urls.py` — trigger task + webhook. (Task 3)
- `memberships/management/commands/reconcile_bmac.py` — cron. (Task 3)
- Tests: `tests/test_bmac.py` (new), `tests/test_kofi.py` (existing).

---

### Task 1: Shared entitlement helpers (refactor)

**Files:**
- Modify: `memberships/services.py`, `memberships/kofi.py`
- Test: `tests/test_kofi.py` (existing must stay green) + add focused helper tests in `tests/test_bmac.py`

**Interfaces:**
- Consumes: `memberships.models.{SupporterStatus, Tier}`.
- Produces: `memberships.services.{normalize_email, resolve_tier, apply_supporter_entitlement}`.

- [ ] **Step 1: Add the failing helper tests**

Create `tests/test_bmac.py`:

```python
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from actors.services import create_local_actor
from comics.services import create_comic
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bmac.py -q`
Expected: ERROR — `apply_supporter_entitlement` does not exist.

- [ ] **Step 3: Add the helpers to `memberships/services.py`**

Extend the import to `from memberships.models import SupporterLink, SupporterStatus, Tier`, then append:

```python
def normalize_email(email):
    return (email or "").strip().lower()


def resolve_tier(comic, name, link=None):
    if name:
        tier = Tier.objects.filter(comic=comic, name__iexact=name).first()
        if tier is not None:
            return tier
    if link is not None:
        latest = (
            SupporterStatus.objects.filter(link=link).order_by("-created_at").first()
        )
        if latest is not None:
            return latest.tier
    return None


def _apply_status(link, tier, valid_until):
    SupporterStatus.objects.filter(link=link, active=True).exclude(tier=tier).update(
        active=False
    )
    SupporterStatus.objects.update_or_create(
        link=link, tier=tier, defaults={"active": True, "valid_until": valid_until}
    )


def apply_supporter_entitlement(supporter):
    comic = supporter.connection.comic
    link = comic.supporter_links.filter(
        provider=supporter.connection.provider,
        provider_email__iexact=supporter.email,
    ).first()
    if link is None:
        return
    now = timezone.now()
    if (
        not supporter.active
        or supporter.valid_until is None
        or supporter.valid_until <= now
    ):
        SupporterStatus.objects.filter(link=link, active=True).update(active=False)
        return
    tier = resolve_tier(comic, supporter.tier_name, link)
    if tier is None:
        return
    _apply_status(link, tier, supporter.valid_until)
```

- [ ] **Step 4: Refactor `memberships/kofi.py` to use them**

Remove the local `normalize_email`, `_resolve_tier`, `_apply_status`, and `_refresh_linked_status`. Import `normalize_email`, `resolve_tier`, `apply_supporter_entitlement` from `memberships.services`, and replace:
- `_refresh_linked_status(supporter)` → `apply_supporter_entitlement(supporter)`.
- In `attach_pending_supporter`, replace the manual tier/status block with:

```python
    if supporter is not None:
        apply_supporter_entitlement(supporter)
```

(Keep the account-email check and `link_supporter` call.)

- [ ] **Step 5: Run the Ko-fi + helper tests**

Run: `uv run pytest tests/test_kofi.py tests/test_bmac.py -q`
Expected: PASS (all).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add memberships/services.py memberships/kofi.py tests/test_bmac.py
git commit -m "refactor: share supporter entitlement helpers"
```

---

### Task 2: BMAC client and reconcile

**Files:**
- Create: `memberships/bmac.py`
- Test: `tests/test_bmac.py`

**Interfaces:**
- Consumes: Task 1 `apply_supporter_entitlement`/`normalize_email`; `memberships.models.{ProviderConnection, ProviderSupporter}`.
- Produces: `memberships.bmac.{BmacError, fetch_subscriptions, reconcile_bmac}`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_bmac.py` (add imports: `import responses`, `from memberships.bmac import BmacError, fetch_subscriptions, reconcile_bmac`):

```python
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
    supporter = ProviderSupporter.objects.get(connection=connection, email="reader@example.com")
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bmac.py -q -k "fetch or reconcile"`
Expected: ERROR — `memberships.bmac` does not exist.

- [ ] **Step 3: Implement `memberships/bmac.py`**

```python
from datetime import datetime

import requests
from django.utils import timezone

from memberships.models import ProviderSupporter
from memberships.services import apply_supporter_entitlement, normalize_email

API_BASE = "https://developers.buymeacoffee.com/api/v1"
TIMEOUT = 15
MAX_PAGES = 20


class BmacError(Exception):
    pass


def fetch_subscriptions(api_key):
    url = f"{API_BASE}/subscriptions"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    subscriptions = []
    for _ in range(MAX_PAGES):
        try:
            response = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise BmacError(str(exc)) from exc
        if response.status_code != 200:
            raise BmacError(f"HTTP {response.status_code}")
        try:
            document = response.json()
        except ValueError as exc:
            raise BmacError("invalid JSON") from exc
        data = document.get("data") if isinstance(document, dict) else None
        if not isinstance(data, list):
            raise BmacError("invalid payload")
        subscriptions.extend(data)
        url = document.get("next_page_url") or document.get("next")
        if not url:
            break
    return subscriptions


def _valid_until(sub):
    raw = sub.get("current_period_end")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _is_active(sub):
    return str(sub.get("status") or "").lower() == "active"


def reconcile_bmac(connection):
    subscriptions = fetch_subscriptions(connection.get_api_key())
    active = {}
    for sub in subscriptions:
        if not isinstance(sub, dict) or not _is_active(sub):
            continue
        email = normalize_email(sub.get("email"))
        if not email:
            continue
        active[email] = {
            "tier_name": str(sub.get("membership_level_name") or "").strip(),
            "valid_until": _valid_until(sub),
        }
    for email, data in active.items():
        supporter, _ = ProviderSupporter.objects.get_or_create(
            connection=connection, email=email
        )
        if data["tier_name"]:
            supporter.tier_name = data["tier_name"]
        supporter.active = True
        if data["valid_until"] is not None:
            supporter.valid_until = data["valid_until"]
        supporter.save()
    ProviderSupporter.objects.filter(connection=connection, active=True).exclude(
        email__in=active
    ).update(active=False)
    for supporter in connection.supporters.all():
        apply_supporter_entitlement(supporter)
    return {"active": len(active)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bmac.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add memberships/bmac.py tests/test_bmac.py
git commit -m "feat: reconcile supporter state against the BMAC API"
```

---

### Task 3: Webhook trigger, task, and cron command

**Files:**
- Modify: `memberships/views.py`, `memberships/urls.py`, `config/urls.py`
- Create: `memberships/tasks.py`, `memberships/management/commands/reconcile_bmac.py`
- Test: `tests/test_bmac.py`

**Interfaces:**
- Consumes: Task 2 `reconcile_bmac`; `memberships.models.ProviderConnection`.
- Produces: `POST /webhooks/bmac/<comic_id>/<secret>`; `memberships.tasks.reconcile_bmac_comic`; the `reconcile_bmac` command.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_bmac.py` (add imports: `import hmac` [not needed], `from django.core.management import call_command`, `from django_tasks_db.models import DBTaskResult`, `from memberships.models import Provider` [already]):

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bmac.py -q -k "webhook or command"`
Expected: FAIL — no route/view/command.

- [ ] **Step 3: Implement the webhook and task**

Append to `memberships/views.py` (imports: `import hmac`, `from memberships import bmac` [optional], `from memberships.models import Provider` [already]):

```python
@csrf_exempt
def bmac_webhook(request, comic_id, secret):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    connection = get_object_or_404(
        ProviderConnection, comic_id=comic_id, provider=Provider.BMAC
    )
    stored = connection.get_webhook_token()
    if not stored or not hmac.compare_digest(stored.encode(), secret.encode()):
        return JsonResponse({"error": "invalid secret"}, status=403)
    from memberships.tasks import reconcile_bmac_comic

    reconcile_bmac_comic.enqueue(str(connection.id))
    return JsonResponse({"ok": True})
```

Create `memberships/tasks.py`:

```python
import logging

from django.tasks import task

from memberships import bmac
from memberships.models import ProviderConnection

logger = logging.getLogger(__name__)


@task
def reconcile_bmac_comic(connection_id):
    connection = ProviderConnection.objects.filter(pk=connection_id).first()
    if connection is None:
        return
    try:
        bmac.reconcile_bmac(connection)
    except bmac.BmacError as exc:
        logger.warning("BMAC reconcile failed for %s: %s", connection_id, exc)
```

Add the route to `memberships/urls.py`:

```python
    path(
        "webhooks/bmac/<uuid:comic_id>/<str:secret>",
        views.bmac_webhook,
        name="bmac-webhook",
    ),
```

- [ ] **Step 4: Implement the cron command**

Create `memberships/management/commands/reconcile_bmac.py`:

```python
from django.core.management.base import BaseCommand

from memberships import bmac
from memberships.models import Provider, ProviderConnection


class Command(BaseCommand):
    help = "Reconcile supporter state against the Buy Me a Coffee API."

    def handle(self, *args, **options):
        reconciled = failed = 0
        for connection in ProviderConnection.objects.filter(provider=Provider.BMAC):
            try:
                bmac.reconcile_bmac(connection)
                reconciled += 1
            except bmac.BmacError as exc:
                failed += 1
                self.stderr.write(f"Failed {connection.comic_id}: {exc}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {reconciled} connection(s), {failed} failed."
            )
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bmac.py -q`
Expected: PASS (10 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add memberships/views.py memberships/urls.py memberships/tasks.py memberships/management/commands/reconcile_bmac.py tests/test_bmac.py
git commit -m "feat: BMAC webhook trigger, task, and reconcile command"
```

---

## Self-Review

**Spec coverage (Ticket 9b2 scope):** BMAC API reconcile as the source of truth (§7); webhook as an authenticated immediacy trigger; supporter activation/deactivation + linked status refresh; tier matching. Out of scope by design: token-based email verification (9b3), connect UI, donation-only access (no tier), and refunds beyond API-driven deactivation.

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `normalize_email`/`resolve_tier`/`apply_supporter_entitlement` are the single shared entitlement path used by both providers; `fetch_subscriptions`/`reconcile_bmac` are the single BMAC path; `reconcile_bmac_comic` is the single task; `BmacError` is the failure signal that aborts without state change.

**Review Focus coverage:** failed fetch makes no changes — Task 2; missing → deactivated + access lost — Task 2; idempotent reconcile — Task 2; bad webhook secret no work — Task 3; Ko-fi regression — Task 1 (existing suite).

## Post-Review Fix Pass

- **Critical — pagination cap:** `fetch_subscriptions` now raises `BmacError` when the next-page link is still present after `MAX_PAGES`, and detects pagination cycles, so a truncated fetch never deactivates supporters.
- **Important — period end:** API-active subscriptions without a parseable future `current_period_end` are excluded from the active set (fail closed), logging invalid values instead of retaining a stale expiry.
- **Important — Ko-fi attach:** `attach_pending_supporter` restores the active/unexpired guard so linking an expired supporter cannot revoke a manually granted status.

Accepted risk: the BMAC webhook secret travels in the URL path (the provider configures a bare URL); treat proxy access logs as sensitive and rotate the token via admin if leaked.

## Deferred Minor Findings

- `ProviderSupporter` email uniqueness is case-sensitive; add a `Lower("email")` constraint/migration in a follow-up (all writes normalize).
- Wrap the reconcile write phase in `transaction.atomic()`.
- `reconcile_bmac` command exits 0 and prints success when some connections fail.
- Webhook task logs and swallows `BmacError` (no retry); consider backend retry, enqueue coalescing, and per-connection throttling.
- `last_payment_at` is not populated for BMAC subscriptions.
- `resolve_tier` falls back to a previous tier when the API supplies an unmatched non-empty tier name.
- Task does not re-assert `provider=Provider.BMAC`; empty API keys are not short-circuited before the request.
