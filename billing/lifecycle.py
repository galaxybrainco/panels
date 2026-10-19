from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from billing import notifications
from billing.models import ComicPlan, PlanStatus

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


def freeze_due_plans(now=None):
    now = now or timezone.now()
    frozen = 0
    for plan in ComicPlan.objects.filter(status=PAST_DUE, grace_until__lte=now):
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
