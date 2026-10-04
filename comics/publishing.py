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
def publish(page, *, published_by=None, when=None):
    """Lock and transition a page to PUBLISHED. Idempotent; enforces gates."""
    locked = Page.objects.select_for_update().get(pk=page.pk)
    if locked.status == PageStatus.PUBLISHED:
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


def unpublish_page(user, page):
    if not permissions.can_publish(user, page.series.comic):
        raise PermissionDenied("You cannot unpublish this page.")
    page.status = PageStatus.DRAFT
    page.published_at = None
    page.scheduled_for = None
    page.save(update_fields=["status", "published_at", "scheduled_for", "updated_at"])
    return page
