from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Sum

from media.models import Media

DEFAULT_QUOTA_BYTES = 10 * 1024**3


def comic_storage_bytes(comic):
    total = Media.objects.filter(page__series__comic=comic).aggregate(
        total=Sum("bytes")
    )["total"]
    return total or 0


def comic_storage_quota(comic):
    plan = _active_plan(comic)
    if plan is not None:
        return plan.quota_bytes
    return getattr(settings, "MEDIA_STORAGE_QUOTA_BYTES", DEFAULT_QUOTA_BYTES)


def _active_plan(comic):
    from memberships.models import ComicPlan, PlanStatus

    plan = ComicPlan.objects.filter(comic=comic).first()
    if plan is not None and plan.status == PlanStatus.ACTIVE:
        return plan
    return None


def ensure_within_quota(comic, additional_bytes):
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
