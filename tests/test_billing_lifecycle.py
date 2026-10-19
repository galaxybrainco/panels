import hashlib
import hmac
import json
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
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

    assert _post_event(client, "invoice.payment_failed", _invoice()).status_code == 200
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

    _post_event(client, "customer.subscription.updated", _subscription(comic, "unpaid"))

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


@override_settings(**WEBHOOK_SETTINGS)
@pytest.mark.django_db
def test_invoice_payment_failed_reads_current_stripe_shape(client):
    owner, comic, _ = _scene()
    comic.plan.delete()
    _active_plan(client, comic)
    mail.outbox.clear()
    invoice = {
        "id": "in_1",
        "object": "invoice",
        "parent": {"subscription_details": {"subscription": "sub_1"}},
    }

    response = _post_event(client, "invoice.payment_failed", invoice)

    assert response.status_code == 200
    plan = ComicPlan.objects.get(comic=comic)
    assert plan.status == PlanStatus.PAST_DUE
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [owner.email]


@override_settings(**WEBHOOK_SETTINGS)
@pytest.mark.django_db
def test_invoice_payment_failed_reads_expanded_current_shape(client):
    _, comic, _ = _scene()
    comic.plan.delete()
    _active_plan(client, comic)
    invoice = {
        "id": "in_1",
        "object": "invoice",
        "parent": {
            "subscription_details": {"subscription": {"id": "sub_1"}},
        },
    }

    _post_event(client, "invoice.payment_failed", invoice)

    assert ComicPlan.objects.get(comic=comic).status == PlanStatus.PAST_DUE


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_stale_instance_does_not_email_twice_or_extend_grace():
    _, _, plan = _scene()
    stale = ComicPlan.objects.get(pk=plan.pk)
    assert lifecycle.apply_status(plan, PlanStatus.PAST_DUE) is True
    plan.refresh_from_db()
    first = plan.grace_until

    assert lifecycle.apply_status(stale, PlanStatus.PAST_DUE) is False
    plan.refresh_from_db()
    assert plan.grace_until == first
    assert len(mail.outbox) == 1


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_past_due_without_grace_gets_one_on_next_failure():
    _, _, plan = _scene(PlanStatus.PAST_DUE)
    assert lifecycle.apply_status(plan, PlanStatus.PAST_DUE) is True
    plan.refresh_from_db()
    assert plan.grace_until is not None
    assert len(mail.outbox) == 1


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_freeze_sweep_skips_plan_restored_after_query(monkeypatch):
    _, _, plan = _scene(PlanStatus.ACTIVE)
    monkeypatch.setattr(lifecycle, "_due_plan_ids", lambda now: [plan.pk])
    assert lifecycle.freeze_due_plans() == 0
    plan.refresh_from_db()
    assert plan.status == PlanStatus.ACTIVE
    assert mail.outbox == []


@override_settings(**BILLING_LIFECYCLE)
@pytest.mark.django_db
def test_archive_sweep_skips_plan_restored_after_query(monkeypatch):
    _, _, plan = _scene(
        PlanStatus.PAST_DUE, grace_until=timezone.now() + timedelta(days=1)
    )
    monkeypatch.setattr(lifecycle, "_stale_frozen_ids", lambda now: [plan.pk])
    assert lifecycle.archive_old_plans() == 0
    plan.refresh_from_db()
    assert plan.status == PlanStatus.PAST_DUE
