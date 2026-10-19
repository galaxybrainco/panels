# Plan-Lapse Lifecycle (9c2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a comic's paid plan a non-destructive lapse ladder — dunning/grace, upload freeze, auto-archive, and one owner notice per transition.

**Architecture:** `billing.lifecycle.apply_status` is the single state machine for `ComicPlan`; `billing.notifications` sends owner emails best-effort; `billing.webhooks` delegates status changes to the state machine (including `invoice.payment_failed`); a cron command advances expired grace and old freezes; `media.quota` rejects uploads for frozen/archived plans. Existing serving, editing, publishing, and federation are untouched.

**Tech Stack:** Python 3.14, Django 6.1.1, Stripe SDK events via the existing seam, Django management commands, Django locmem mailer for tests.

**Spec:** `docs/superpowers/specs/2026-10-18-plan-lifecycle-design.md`

## Global Constraints

- Python 3.14, Django 6.1.1; run everything through `uv run`.
- Settings are env-driven; never hardcode grace/archive days.
- `billing.lifecycle.apply_status` is the only place that changes `ComicPlan.status`, `grace_until`, or `frozen_at`.
- Notices fire only on real transitions; repeated failures and webhook replays must never email twice.
- Email failures are logged and swallowed; they must not break a transition or a webhook response.
- `FROZEN`/`ARCHIVED` block new uploads only. Editing, removing media, adding pages, publishing, serving, and federation keep working.
- `PAST_DUE` keeps the plan's quota during grace.
- Keep `ruff check .` and `ruff format --check .` clean; run `uv run python manage.py makemigrations --check --dry-run` before final commits.

## Review Focus

- **Notice storms:** repeated `invoice.payment_failed`, replayed subscription events, and late `unpaid` updates after a freeze must not re-email or reset grace.
- **Non-destructive freeze:** frozen/archived comics must still edit, delete media, publish, serve AP pages, and federate; only new uploads fail.
- **Grace boundary:** `grace_until` exactly now, null grace, and repeated command runs must be deterministic and idempotent.
- **Restore:** a paid subscription after past-due, freeze, or archive clears all timestamps and re-enables uploads.
- **Stray events:** invoices for unknown/absent subscriptions, plans whose comic has no owner, and malformed metadata must be ignored without errors.

---

### Task 1: Lifecycle state machine, timestamps, and owner notices

**Files:**
- Modify: `billing/models.py`
- Create: `billing/migrations/0003_plan_lifecycle.py` (generated)
- Modify: `config/settings/base.py`
- Create: `billing/notifications.py`
- Create: `billing/lifecycle.py`
- Create: `tests/test_billing_lifecycle.py`

**Interfaces:**
- Consumes: `ComicPlan`, `PlanStatus`, `comics.models.ComicRole`, `DEFAULT_FROM_EMAIL`, `INSTANCE_URL`.
- Produces:
  - `ComicPlan.grace_until` and `ComicPlan.frozen_at` (nullable datetimes).
  - settings `BILLING_GRACE_DAYS`, `BILLING_ARCHIVE_AFTER_DAYS`.
  - `billing.notifications.notify_past_due(plan, grace_until)`, `notify_frozen(plan)`, `notify_restored(plan)` → `bool`.
  - `billing.lifecycle.apply_status(plan, new_status, now=None)` → `bool` (True when the status changed).

- [ ] **Step 1: Write the failing lifecycle tests**

Create `tests/test_billing_lifecycle.py`:

```python
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from billing import lifecycle, notifications
from billing.models import ComicPlan, PlanStatus
from comics.services import create_comic

BILLING_LIFECYCLE = {
    "BILLING_GRACE_DAYS": 7,
    "BILLING_ARCHIVE_AFTER_DAYS": 90,
    "DEFAULT_FROM_EMAIL": "billing@panels.test",
}


def _scene(status=PlanStatus.ACTIVE, **kwargs):
    owner = get_user_model().objects.create_user(
        email=f"o-{uuid4().hex[:8]}@example.com", password="x"
    )
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    plan = ComicPlan.objects.create(
        comic=comic, status=status, quota_bytes=10 * 1024**3, **kwargs
    )
    return owner, comic, plan


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_past_due_starts_grace_and_emails_owner():
    owner, comic, plan = _scene()
    changed = lifecycle.apply_status(plan, PlanStatus.PAST_DUE)
    plan.refresh_from_db()
    assert changed is True
    assert plan.status == PlanStatus.PAST_DUE
    assert plan.grace_until is not None
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [owner.email]
    assert comic.title in mail.outbox[0].subject


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_repeated_past_due_does_not_extend_grace_or_reemail():
    _, _, plan = _scene()
    lifecycle.apply_status(plan, PlanStatus.PAST_DUE)
    plan.refresh_from_db()
    first = plan.grace_until
    assert lifecycle.apply_status(plan, PlanStatus.PAST_DUE) is False
    plan.refresh_from_db()
    assert plan.grace_until == first
    assert len(mail.outbox) == 1


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_active_to_active_is_a_noop():
    _, _, plan = _scene()
    assert lifecycle.apply_status(plan, PlanStatus.ACTIVE) is False
    assert mail.outbox == []


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_restore_clears_timestamps_and_emails_owner():
    _, _, plan = _scene(
        PlanStatus.PAST_DUE,
        grace_until=timezone.now() + timedelta(days=3),
        frozen_at=timezone.now(),
    )
    assert lifecycle.apply_status(plan, PlanStatus.ACTIVE) is True
    plan.refresh_from_db()
    assert plan.status == PlanStatus.ACTIVE
    assert plan.grace_until is None
    assert plan.frozen_at is None
    assert len(mail.outbox) == 1


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_frozen_plan_refuses_to_demote_to_past_due():
    _, _, plan = _scene(PlanStatus.FROZEN, frozen_at=timezone.now())
    assert lifecycle.apply_status(plan, PlanStatus.PAST_DUE) is False
    plan.refresh_from_db()
    assert plan.status == PlanStatus.FROZEN
    assert mail.outbox == []


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_freeze_sets_timestamp_and_emails_owner():
    _, _, plan = _scene(
        PlanStatus.PAST_DUE, grace_until=timezone.now() - timedelta(seconds=1)
    )
    assert lifecycle.apply_status(plan, PlanStatus.FROZEN) is True
    plan.refresh_from_db()
    assert plan.status == PlanStatus.FROZEN
    assert plan.frozen_at is not None
    assert len(mail.outbox) == 1


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_archive_is_quiet():
    _, _, plan = _scene(PlanStatus.FROZEN, frozen_at=timezone.now())
    assert lifecycle.apply_status(plan, PlanStatus.ARCHIVED) is True
    plan.refresh_from_db()
    assert plan.status == PlanStatus.ARCHIVED
    assert mail.outbox == []


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_comic_without_owner_gets_no_email():
    _, comic, plan = _scene()
    comic.roles.all().delete()
    assert lifecycle.apply_status(plan, PlanStatus.PAST_DUE) is True
    assert mail.outbox == []


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_email_failure_does_not_block_the_transition(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notifications, "send_mail", broken)
    _, _, plan = _scene()
    assert lifecycle.apply_status(plan, PlanStatus.PAST_DUE) is True
    plan.refresh_from_db()
    assert plan.status == PlanStatus.PAST_DUE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_billing_lifecycle.py -q`
Expected: `ImportError: cannot import name 'lifecycle'` (or the missing `grace_until` field).

- [ ] **Step 3: Add the model fields and settings**

In `billing/models.py`, add to `ComicPlan` after `current_period_end`:

```python
    grace_until = models.DateTimeField(null=True, blank=True)
    frozen_at = models.DateTimeField(null=True, blank=True)
```

In `config/settings/base.py`, after `BILLING_PORTAL_RETURN_URL`, add:

```python
BILLING_GRACE_DAYS = env.int("BILLING_GRACE_DAYS", default=7)
BILLING_ARCHIVE_AFTER_DAYS = env.int("BILLING_ARCHIVE_AFTER_DAYS", default=90)
```

Run: `uv run python manage.py makemigrations billing`
Expected: creates `billing/migrations/0003_plan_lifecycle.py`.

- [ ] **Step 4: Implement the notifications**

Create `billing/notifications.py`:

```python
import logging

from django.conf import settings
from django.core.mail import send_mail

from comics.models import ComicRole

logger = logging.getLogger(__name__)


def _owner_email(comic):
    role = (
        comic.roles.filter(role=ComicRole.Role.OWNER)
        .select_related("user")
        .first()
    )
    if role is None:
        return ""
    return role.user.email or ""


def _send(plan, subject, body):
    email = _owner_email(plan.comic)
    if not email:
        return False
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email])
    except Exception:
        logger.exception("Failed to send billing notice for plan %s", plan.pk)
        return False
    return True


def notify_past_due(plan, grace_until):
    subject = f"Payment failed for {plan.comic.title}"
    body = (
        f"The plan for {plan.comic.title} could not be renewed.\n"
        f"Uploads stay enabled until {grace_until:%Y-%m-%d %H:%M} UTC.\n"
        f"Update billing: {settings.INSTANCE_URL}\n"
    )
    return _send(plan, subject, body)


def notify_frozen(plan):
    subject = f"{plan.comic.title} is read-only"
    body = (
        f"The plan for {plan.comic.title} is frozen: new uploads are disabled.\n"
        "The comic still serves and federates existing pages.\n"
        f"Update billing: {settings.INSTANCE_URL}\n"
    )
    return _send(plan, subject, body)


def notify_restored(plan):
    subject = f"{plan.comic.title} is active again"
    body = (
        f"The plan for {plan.comic.title} is active again and uploads work.\n"
        f"Manage billing: {settings.INSTANCE_URL}\n"
    )
    return _send(plan, subject, body)
```

- [ ] **Step 5: Implement the state machine**

Create `billing/lifecycle.py`:

```python
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from billing import notifications
from billing.models import PlanStatus

ACTIVE = PlanStatus.ACTIVE
PAST_DUE = PlanStatus.PAST_DUE
FROZEN = PlanStatus.FROZEN
ARCHIVED = PlanStatus.ARCHIVED

TERMINAL = {FROZEN, ARCHIVED}


def apply_status(plan, new_status, now=None):
    now = now or timezone.now()
    previous = plan.status
    if new_status == previous:
        return False
    if new_status == PAST_DUE and previous in TERMINAL:
        return False
    if new_status == ACTIVE:
        plan.status = ACTIVE
        plan.grace_until = None
        plan.frozen_at = None
        plan.save(update_fields=["status", "grace_until", "frozen_at"])
        notifications.notify_restored(plan)
        return True
    if new_status == PAST_DUE:
        plan.status = PAST_DUE
        plan.grace_until = now + timedelta(days=settings.BILLING_GRACE_DAYS)
        plan.save(update_fields=["status", "grace_until"])
        notifications.notify_past_due(plan, plan.grace_until)
        return True
    if new_status == FROZEN:
        plan.status = FROZEN
        plan.frozen_at = now
        plan.save(update_fields=["status", "frozen_at"])
        notifications.notify_frozen(plan)
        return True
    if new_status == ARCHIVED:
        plan.status = ARCHIVED
        plan.save(update_fields=["status"])
        return True
    return False
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_billing_lifecycle.py -q`
Expected: `9 passed`

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add billing/models.py billing/migrations/0003_plan_lifecycle.py config/settings/base.py billing/notifications.py billing/lifecycle.py tests/test_billing_lifecycle.py
git commit -m "feat: add plan lifecycle state machine and owner notices"
```

---

### Task 2: Webhook lifecycle integration

**Files:**
- Modify: `billing/webhooks.py`
- Modify: `billing/views.py`
- Modify: `tests/test_billing_lifecycle.py`

**Interfaces:**
- Consumes: `lifecycle.apply_status`, `ComicPlan`, `PlanStatus`.
- Produces: `billing.webhooks.PAYMENT_EVENTS` (set with `"invoice.payment_failed"`); `billing.webhooks.handle_payment_failed(invoice)` → `ComicPlan | None`; subscription sync delegates status to `apply_status`.

- [ ] **Step 1: Add failing webhook tests**

Append to `tests/test_billing_lifecycle.py`:

```python
import hashlib
import hmac
import json
import time

WEBHOOK_SECRET = "whsec_test"
WEBHOOK_SETTINGS = {
    **BILLING_LIFECYCLE,
    "BILLING_ENABLED": True,
    "STRIPE_SECRET_KEY": "sk_test_123",
    "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET,
    "BILLING_PRICE_MONTHLY": "price_month",
    "BILLING_PRICE_ANNUAL": "price_year",
}


def _signature(payload, secret=WEBHOOK_SECRET):
    timestamp = int(time.time())
    digest = hmac.new(
        secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _post_event(client, event_type, obj):
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


def _subscription(comic, status="active", subscription_id="sub_1"):
    return {
        "id": subscription_id,
        "object": "subscription",
        "customer": "cus_1",
        "status": status,
        "metadata": {"comic_id": str(comic.id)},
        "current_period_end": 1893456000,
    }


def _invoice(subscription_id="sub_1"):
    return {
        "id": "in_1",
        "object": "invoice",
        "customer": "cus_1",
        "subscription": subscription_id,
    }


def _active_plan(client, comic, **kwargs):
    assert (
        _post_event(
            client, "customer.subscription.created", _subscription(comic, **kwargs)
        ).status_code
        == 200
    )


@override_settings(**WEBHOOK_SETTINGS)
@pytest.mark.django_db
def test_invoice_payment_failed_starts_grace_and_emails(client):
    owner, comic, _ = _scene()
    comic.plan.delete()
    _active_plan(client, comic)
    mail.outbox.clear()

    response = _post_event(client, "invoice.payment_failed", _invoice())

    assert response.status_code == 200
    plan = ComicPlan.objects.get(comic=comic)
    assert plan.status == PlanStatus.PAST_DUE
    assert plan.grace_until is not None
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [owner.email]


@override_settings(**WEBHOOK_SETTINGS)
@pytest.mark.django_db
def test_repeated_invoice_failure_keeps_grace_and_does_not_reemail(client):
    _, comic, _ = _scene()
    comic.plan.delete()
    _active_plan(client, comic)
    _post_event(client, "invoice.payment_failed", _invoice())
    mail.outbox.clear()
    plan = ComicPlan.objects.get(comic=comic)
    first = plan.grace_until

    assert (
        _post_event(client, "invoice.payment_failed", _invoice()).status_code == 200
    )
    plan.refresh_from_db()
    assert plan.grace_until == first
    assert mail.outbox == []


@override_settings(**WEBHOOK_SETTINGS)
@pytest.mark.django_db
def test_past_due_subscription_update_starts_grace_and_emails(client):
    owner, comic, _ = _scene()
    comic.plan.delete()
    _active_plan(client, comic)
    mail.outbox.clear()

    response = _post_event(
        client, "customer.subscription.updated", _subscription(comic, "past_due")
    )

    assert response.status_code == 200
    plan = ComicPlan.objects.get(comic=comic)
    assert plan.status == PlanStatus.PAST_DUE
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [owner.email]


@override_settings(**WEBHOOK_SETTINGS)
@pytest.mark.django_db
def test_active_subscription_restores_frozen_plan_and_emails(client):
    owner, comic, _ = _scene()
    comic.plan.delete()
    _active_plan(client, comic)
    _post_event(client, "invoice.payment_failed", _invoice())
    plan = ComicPlan.objects.get(comic=comic)
    lifecycle.apply_status(plan, PlanStatus.FROZEN)
    mail.outbox.clear()

    response = _post_event(
        client, "customer.subscription.updated", _subscription(comic)
    )

    assert response.status_code == 200
    plan.refresh_from_db()
    assert plan.status == PlanStatus.ACTIVE
    assert plan.grace_until is None
    assert plan.frozen_at is None
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [owner.email]


@override_settings(**WEBHOOK_SETTINGS)
@pytest.mark.django_db
def test_frozen_plan_ignores_non_paid_subscription_update(client):
    _, comic, _ = _scene()
    comic.plan.delete()
    _active_plan(client, comic)
    plan = ComicPlan.objects.get(comic=comic)
    lifecycle.apply_status(plan, PlanStatus.PAST_DUE)
    lifecycle.apply_status(plan, PlanStatus.FROZEN)
    mail.outbox.clear()

    _post_event(
        client, "customer.subscription.updated", _subscription(comic, "unpaid")
    )

    plan.refresh_from_db()
    assert plan.status == PlanStatus.FROZEN
    assert mail.outbox == []


@override_settings(**WEBHOOK_SETTINGS)
@pytest.mark.django_db
def test_invoice_for_unknown_subscription_is_ignored(client):
    response = _post_event(
        client, "invoice.payment_failed", _invoice(subscription_id="sub_missing")
    )
    assert response.status_code == 200
    assert ComicPlan.objects.count() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_billing_lifecycle.py -q`
Expected: failures around `invoice.payment_failed` not starting grace (status stays ACTIVE) and the frozen plan being demoted.

- [ ] **Step 3: Refactor subscription sync and add invoice handling**

In `billing/webhooks.py`, add the import and event set:

```python
from billing import lifecycle
```

```python
PAYMENT_EVENTS = {"invoice.payment_failed"}
```

Replace the body of `sync_subscription` after the `period_end` line with:

```python
    status = plan_status_for(_get(subscription, "status"))
    plan, created = ComicPlan.objects.get_or_create(
        comic=comic,
        defaults={
            "quota_bytes": settings.MEDIA_STORAGE_QUOTA_BYTES,
            "stripe_customer_id": customer or "",
            "stripe_subscription_id": subscription_id or "",
            "current_period_end": period_end,
        },
    )
    if not created:
        plan.stripe_customer_id = customer or plan.stripe_customer_id
        plan.stripe_subscription_id = subscription_id or plan.stripe_subscription_id
        if period_end is not None:
            plan.current_period_end = period_end
        plan.save(
            update_fields=[
                "stripe_customer_id",
                "stripe_subscription_id",
                "current_period_end",
            ]
        )
    lifecycle.apply_status(plan, status)
    return plan
```

Then append:

```python
def handle_payment_failed(invoice):
    subscription = _get(invoice, "subscription")
    if not isinstance(subscription, str):
        subscription = _get(subscription, "id")
    if not subscription:
        return None
    plan = ComicPlan.objects.filter(stripe_subscription_id=subscription).first()
    if plan is None:
        return None
    lifecycle.apply_status(plan, PlanStatus.PAST_DUE)
    return plan
```

In `billing/views.py`, extend the event dispatch:

```python
    if event["type"] in webhooks.SUBSCRIPTION_EVENTS:
        webhooks.sync_subscription(event["data"]["object"])
    elif event["type"] in webhooks.PAYMENT_EVENTS:
        webhooks.handle_payment_failed(event["data"]["object"])
```

Note: a newly created plan defaults to `ACTIVE` (the field default) and then
`apply_status` transitions it to the mapped status, so a first event that is
already `past_due` still gets a grace window and a notice.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_billing_lifecycle.py tests/test_billing.py -q`
Expected: all pass (47 tests).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add billing/webhooks.py billing/views.py tests/test_billing_lifecycle.py
git commit -m "feat: drive plan grace and freeze from Stripe events"
```

---

### Task 3: Lifecycle command (freeze due, archive old)

**Files:**
- Create: `billing/management/__init__.py`
- Create: `billing/management/commands/__init__.py`
- Create: `billing/management/commands/enforce_plan_lifecycle.py`
- Modify: `billing/lifecycle.py`
- Modify: `tests/test_billing_lifecycle.py`

**Interfaces:**
- Consumes: `apply_status`, `ComicPlan`, `PlanStatus`, `BILLING_ARCHIVE_AFTER_DAYS`.
- Produces: `lifecycle.freeze_due_plans(now=None)` → `int`; `lifecycle.archive_old_plans(now=None)` → `int`; command `enforce_plan_lifecycle`.

- [ ] **Step 1: Add failing command tests**

Append to `tests/test_billing_lifecycle.py`:

```python
from django.core.management import call_command


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_command_freezes_expired_grace_and_emails(capsys):
    owner, _, plan = _scene(
        PlanStatus.PAST_DUE, grace_until=timezone.now() - timedelta(seconds=1)
    )
    call_command("enforce_plan_lifecycle")
    plan.refresh_from_db()
    assert plan.status == PlanStatus.FROZEN
    assert plan.frozen_at is not None
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [owner.email]
    assert "Froze 1" in capsys.readouterr().out


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_command_leaves_unexpired_and_null_grace_alone():
    _, _, live = _scene(
        PlanStatus.PAST_DUE, grace_until=timezone.now() + timedelta(days=1)
    )
    _, _, legacy = _scene(PlanStatus.PAST_DUE)
    call_command("enforce_plan_lifecycle")
    live.refresh_from_db()
    legacy.refresh_from_db()
    assert live.status == PlanStatus.PAST_DUE
    assert legacy.status == PlanStatus.PAST_DUE
    assert mail.outbox == []


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_command_archives_old_frozen_plans(capsys):
    _, _, plan = _scene(
        PlanStatus.FROZEN, frozen_at=timezone.now() - timedelta(days=91)
    )
    call_command("enforce_plan_lifecycle")
    plan.refresh_from_db()
    assert plan.status == PlanStatus.ARCHIVED
    assert mail.outbox == []
    assert "archived 1" in capsys.readouterr().out


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_command_keeps_young_frozen_plans():
    _, _, plan = _scene(
        PlanStatus.FROZEN, frozen_at=timezone.now() - timedelta(days=10)
    )
    call_command("enforce_plan_lifecycle")
    plan.refresh_from_db()
    assert plan.status == PlanStatus.FROZEN


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_command_is_idempotent():
    _, _, plan = _scene(
        PlanStatus.PAST_DUE, grace_until=timezone.now() - timedelta(days=1)
    )
    call_command("enforce_plan_lifecycle")
    mail.outbox.clear()
    call_command("enforce_plan_lifecycle")
    plan.refresh_from_db()
    assert plan.status == PlanStatus.FROZEN
    assert mail.outbox == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_billing_lifecycle.py -q -k command`
Expected: `CommandError: Unknown command: 'enforce_plan_lifecycle'`.

- [ ] **Step 3: Implement the lifecycle helpers and command**

Append to `billing/lifecycle.py`:

```python
def freeze_due_plans(now=None):
    now = now or timezone.now()
    frozen = 0
    for plan in ComicPlan.objects.filter(
        status=PAST_DUE, grace_until__lte=now
    ):
        if apply_status(plan, FROZEN, now):
            frozen += 1
    return frozen


def archive_old_plans(now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.BILLING_ARCHIVE_AFTER_DAYS)
    archived = 0
    for plan in ComicPlan.objects.filter(status=FROZEN, frozen_at__lte=cutoff):
        if apply_status(plan, ARCHIVED, now):
            archived += 1
    return archived
```

Add the import at the top of `billing/lifecycle.py`:

```python
from billing.models import ComicPlan, PlanStatus
```

Create `billing/management/__init__.py` and
`billing/management/commands/__init__.py` as empty files, and
`billing/management/commands/enforce_plan_lifecycle.py`:

```python
from django.core.management.base import BaseCommand

from billing import lifecycle


class Command(BaseCommand):
    help = "Freeze plans past their grace window and archive old freezes."

    def handle(self, *args, **options):
        frozen = lifecycle.freeze_due_plans()
        archived = lifecycle.archive_old_plans()
        self.stdout.write(
            self.style.SUCCESS(f"Froze {frozen} plan(s), archived {archived}.")
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_billing_lifecycle.py -q`
Expected: `20 passed`

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add billing/lifecycle.py billing/management tests/test_billing_lifecycle.py
git commit -m "feat: enforce plan grace and auto-archive via command"
```

---

### Task 4: Upload gate and grace quota

**Files:**
- Modify: `media/quota.py`
- Modify: `tests/test_media_quota.py`

**Interfaces:**
- Consumes: `ComicPlan`, `PlanStatus`.
- Produces: `media.quota.plan_blocks_uploads(comic)` → `bool`; `ensure_within_quota` rejects frozen/archived plans; `comic_storage_quota` honors `quota_bytes` for `ACTIVE` and `PAST_DUE`.

- [ ] **Step 1: Add failing upload tests**

Append to `tests/test_media_quota.py` (imports to add: `from billing.models import ComicPlan, PlanStatus` and `from comics.publishing import publish_page`):

```python
def _plan(comic, status, quota_bytes=10 * 1024**3):
    return ComicPlan.objects.create(
        comic=comic, status=status, quota_bytes=quota_bytes
    )


@pytest.mark.django_db
def test_past_due_plan_can_still_upload():
    owner, comic, page = scene()
    _plan(comic, PlanStatus.PAST_DUE)
    assert add_media(owner, page, upload()) is not None


@pytest.mark.django_db
def test_frozen_plan_rejects_new_uploads():
    owner, comic, page = scene()
    _plan(comic, PlanStatus.FROZEN)
    with pytest.raises(ValidationError) as exc:
        add_media(owner, page, upload())
    assert "read-only" in " ".join(exc.value.message_dict["file"])


@pytest.mark.django_db
def test_archived_plan_rejects_new_uploads():
    owner, comic, page = scene()
    _plan(comic, PlanStatus.ARCHIVED)
    with pytest.raises(ValidationError):
        add_media(owner, page, upload())


@pytest.mark.django_db
def test_frozen_plan_still_allows_media_removal():
    owner, comic, page = scene()
    plan = _plan(comic, PlanStatus.ACTIVE)
    media = add_media(owner, page, upload())
    plan.status = PlanStatus.FROZEN
    plan.save(update_fields=["status"])
    remove_media(owner, media)
    assert comic_storage_bytes(comic) == 0


@pytest.mark.django_db
def test_frozen_plan_still_serves_published_pages(client):
    owner, comic, page = scene()
    plan = _plan(comic, PlanStatus.ACTIVE)
    add_media(owner, page, upload())
    publish_page(owner, page)
    plan.status = PlanStatus.FROZEN
    plan.save(update_fields=["status"])
    response = client.get(f"/pages/{page.id}")
    assert response.status_code == 200


@pytest.mark.django_db
def test_past_due_plan_keeps_its_quota():
    _, comic, _ = scene()
    _plan(comic, PlanStatus.PAST_DUE, quota_bytes=222)
    from media.quota import comic_storage_quota

    assert comic_storage_quota(comic) == 222
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_media_quota.py -q -k "past_due or frozen or archived"`
Expected: frozen/archived uploads succeed today, and the past-due quota is the default instead of 222.

- [ ] **Step 3: Implement the gate**

In `media/quota.py`, add:

```python
def plan_blocks_uploads(comic):
    from billing.models import ComicPlan, PlanStatus

    return ComicPlan.objects.filter(
        comic=comic, status__in=(PlanStatus.FROZEN, PlanStatus.ARCHIVED)
    ).exists()
```

Rename `_active_plan` to `_paid_plan` and widen its filter:

```python
def _paid_plan(comic):
    from billing.models import ComicPlan, PlanStatus

    plan = ComicPlan.objects.filter(comic=comic).first()
    if plan is not None and plan.status in (PlanStatus.ACTIVE, PlanStatus.PAST_DUE):
        return plan
    return None
```

Update `comic_storage_quota` to call `_paid_plan`, and put the gate at the start
of `ensure_within_quota`:

```python
def ensure_within_quota(comic, additional_bytes):
    if plan_blocks_uploads(comic):
        raise ValidationError(
            {"file": "This comic is read-only while its plan is frozen."}
        )
    used = comic_storage_bytes(comic)
    quota = comic_storage_quota(comic)
    if used + additional_bytes > quota:
        raise ValidationError(
            {
                "file": "This comic is out of storage.",
                "used_bytes": used,
                "quota_bytes": quota,
                "needed_bytes": additional_bytes,
            }
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_media_quota.py -q`
Expected: all pass.

- [ ] **Step 5: Run the full suite, lint, and migration check**

Run: `uv run pytest -q`
Expected: previous total + new tests, no failures.

Run: `uv run ruff check .` and `uv run ruff format --check .`
Expected: clean.

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`

- [ ] **Step 6: Commit**

```bash
git add media/quota.py tests/test_media_quota.py
git commit -m "feat: block uploads for frozen and archived plans"
```
