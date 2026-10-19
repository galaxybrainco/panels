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
    plan = _paid_plan(comic)
    if plan is not None:
        return plan.quota_bytes
    return getattr(settings, "MEDIA_STORAGE_QUOTA_BYTES", DEFAULT_QUOTA_BYTES)


def _paid_plan(comic):
    from billing.models import ComicPlan, PlanStatus

    plan = ComicPlan.objects.filter(comic=comic).first()
    if plan is not None and plan.status in (PlanStatus.ACTIVE, PlanStatus.PAST_DUE):
        return plan
    return None


def plan_blocks_uploads(comic):
    from billing.models import ComicPlan, PlanStatus

    return ComicPlan.objects.filter(
        comic=comic, status__in=(PlanStatus.FROZEN, PlanStatus.ARCHIVED)
    ).exists()


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
