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
