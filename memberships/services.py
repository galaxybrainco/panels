from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from federation.activitypub import Audience
from memberships.models import SupporterLink, SupporterStatus


def link_supporter(actor, comic, provider, email):
    link, _ = SupporterLink.objects.update_or_create(
        comic=comic,
        actor=actor,
        defaults={
            "provider": provider,
            "provider_email": email,
            "verified_at": timezone.now(),
        },
    )
    return link


def grant_supporter_status(link, tier, *, valid_until=None, active=True):
    if tier.comic_id != link.comic_id:
        raise ValidationError("The tier belongs to a different comic.")
    return SupporterStatus.objects.create(
        link=link, tier=tier, valid_until=valid_until, active=active
    )


def active_supporter_status(actor, comic):
    if actor is None:
        return None
    now = timezone.now()
    return (
        SupporterStatus.objects.filter(
            link__comic=comic, link__actor=actor, active=True
        )
        .filter(models.Q(valid_until__isnull=True) | models.Q(valid_until__gt=now))
        .select_related("tier")
        .order_by("-tier__rank", "-created_at")
        .first()
    )


def supporter_has_access(actor, comic, tier=None):
    status = active_supporter_status(actor, comic)
    if status is None:
        return False
    if tier is None:
        return True
    return status.tier.rank >= tier.rank


ANY_SUPPORTER = object()


def page_requires_tier(page):
    if page.audience not in (Audience.MEMBERS, Audience.TIER):
        return None
    if page.audience == Audience.TIER and page.required_tier_id:
        return page.required_tier
    return ANY_SUPPORTER


def can_view_page(actor, page):
    required = page_requires_tier(page)
    if required is None:
        return True
    if required is ANY_SUPPORTER:
        return supporter_has_access(actor, page.series.comic)
    return supporter_has_access(actor, page.series.comic, required)
