from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from comics import permissions
from comics.models import Page, PageStatus


def _object_id(page) -> str:
    return f"{settings.INSTANCE_URL}/pages/{page.id}"


def _enforce_publish_gates(page) -> None:
    if not page.alt_text.strip():
        raise ValidationError({"alt_text": "Alt text is required before publishing."})
    if page.sensitive and not page.content_warning.strip():
        raise ValidationError(
            {"content_warning": "A content warning is required for sensitive pages."}
        )


@transaction.atomic
def publish(page, *, published_by=None, when=None, require_scheduled=False):
    """Lock and transition a page to PUBLISHED. Idempotent; enforces gates.

    With ``require_scheduled=True`` the transition is a no-op unless the locked
    row is still SCHEDULED, so a task or sweep cannot resurrect a page that was
    unscheduled after it was read.
    """
    locked = Page.objects.select_for_update().get(pk=page.pk)
    if locked.status == PageStatus.PUBLISHED:
        return locked
    if require_scheduled and locked.status != PageStatus.SCHEDULED:
        return locked
    _enforce_publish_gates(locked)
    locked.status = PageStatus.PUBLISHED
    locked.published_at = when or timezone.now()
    locked.published_by = published_by
    locked.scheduled_for = None
    if not locked.ap_id:
        locked.ap_id = _object_id(locked)
    locked.save(
        update_fields=[
            "status",
            "published_at",
            "published_by",
            "scheduled_for",
            "ap_id",
            "updated_at",
        ]
    )
    return locked


def publish_page(user, page, *, when=None):
    if not permissions.can_publish(user, page.series.comic):
        raise PermissionDenied("You cannot publish this page.")
    return publish(page, published_by=user, when=when)


@transaction.atomic
def unpublish_page(user, page):
    if not permissions.can_publish(user, page.series.comic):
        raise PermissionDenied("You cannot unpublish this page.")
    locked = Page.objects.select_for_update().get(pk=page.pk)
    locked.status = PageStatus.DRAFT
    locked.published_at = None
    locked.scheduled_for = None
    locked.save(update_fields=["status", "published_at", "scheduled_for", "updated_at"])
    return locked


@transaction.atomic
def schedule_page(user, page, when):
    if not permissions.can_publish(user, page.series.comic):
        raise PermissionDenied("You cannot schedule this page.")
    if timezone.is_naive(when):
        raise ValidationError(
            {"scheduled_for": "A timezone-aware datetime is required."}
        )
    if when <= timezone.now():
        raise ValidationError(
            {"scheduled_for": "The scheduled time must be in the future."}
        )
    locked = Page.objects.select_for_update().get(pk=page.pk)
    if locked.status == PageStatus.PUBLISHED:
        raise ValidationError({"status": "Unpublish the page before scheduling it."})
    _enforce_publish_gates(locked)
    from comics.tasks import publish_scheduled_page

    locked.status = PageStatus.SCHEDULED
    locked.scheduled_for = when
    locked.scheduled_by = user
    locked.save(update_fields=["status", "scheduled_for", "scheduled_by", "updated_at"])
    publish_scheduled_page.using(run_after=when).enqueue(str(locked.id))
    return locked


@transaction.atomic
def unschedule_page(user, page):
    if not permissions.can_publish(user, page.series.comic):
        raise PermissionDenied("You cannot unschedule this page.")
    locked = Page.objects.select_for_update().get(pk=page.pk)
    locked.status = PageStatus.DRAFT
    locked.scheduled_for = None
    locked.scheduled_by = None
    locked.save(update_fields=["status", "scheduled_for", "scheduled_by", "updated_at"])
    return locked
