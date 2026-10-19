from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from billing import notifications
from billing.models import ComicPlan, PlanStatus

ACTIVE = PlanStatus.ACTIVE
PAST_DUE = PlanStatus.PAST_DUE
FROZEN = PlanStatus.FROZEN
ARCHIVED = PlanStatus.ARCHIVED

TERMINAL = {FROZEN, ARCHIVED}


def _transition(plan, new_status, now):
    previous = plan.status
    if new_status == previous:
        if new_status == PAST_DUE and plan.grace_until is None:
            plan.grace_until = now + timedelta(days=settings.BILLING_GRACE_DAYS)
            plan.save(update_fields=["grace_until", "updated_at"])
            notifications.notify_past_due(plan, plan.grace_until)
            return True
        return False
    if new_status == PAST_DUE and previous in TERMINAL:
        return False
    if new_status == ACTIVE:
        plan.status = ACTIVE
        plan.grace_until = None
        plan.frozen_at = None
        plan.save(update_fields=["status", "grace_until", "frozen_at", "updated_at"])
        notifications.notify_restored(plan)
        return True
    if new_status == PAST_DUE:
        plan.status = PAST_DUE
        plan.grace_until = now + timedelta(days=settings.BILLING_GRACE_DAYS)
        plan.save(update_fields=["status", "grace_until", "updated_at"])
        notifications.notify_past_due(plan, plan.grace_until)
        return True
    if new_status == FROZEN:
        plan.status = FROZEN
        plan.frozen_at = now
        plan.save(update_fields=["status", "frozen_at", "updated_at"])
        notifications.notify_frozen(plan)
        return True
    if new_status == ARCHIVED:
        plan.status = ARCHIVED
        plan.save(update_fields=["status", "updated_at"])
        return True
    return False


def apply_status(plan, new_status, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        try:
            locked = ComicPlan.objects.select_for_update().get(pk=plan.pk)
        except ComicPlan.DoesNotExist:
            return False
        changed = _transition(locked, new_status, now)
    plan.status = locked.status
    plan.grace_until = locked.grace_until
    plan.frozen_at = locked.frozen_at
    plan.updated_at = locked.updated_at
    return changed


def _due_plan_ids(now):
    return list(
        ComicPlan.objects.filter(status=PAST_DUE, grace_until__lte=now).values_list(
            "pk", flat=True
        )
    )


def _stale_frozen_ids(now):
    cutoff = now - timedelta(days=settings.BILLING_ARCHIVE_AFTER_DAYS)
    return list(
        ComicPlan.objects.filter(status=FROZEN, frozen_at__lte=cutoff).values_list(
            "pk", flat=True
        )
    )


def freeze_due_plans(now=None):
    now = now or timezone.now()
    frozen = 0
    for pk in _due_plan_ids(now):
        with transaction.atomic():
            plan = ComicPlan.objects.select_for_update().get(pk=pk)
            if (
                plan.status != PAST_DUE
                or plan.grace_until is None
                or plan.grace_until > now
            ):
                continue
            if _transition(plan, FROZEN, now):
                frozen += 1
    return frozen


def archive_old_plans(now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.BILLING_ARCHIVE_AFTER_DAYS)
    archived = 0
    for pk in _stale_frozen_ids(now):
        with transaction.atomic():
            plan = ComicPlan.objects.select_for_update().get(pk=pk)
            if (
                plan.status != FROZEN
                or plan.frozen_at is None
                or plan.frozen_at > cutoff
            ):
                continue
            if _transition(plan, ARCHIVED, now):
                archived += 1
    return archived
