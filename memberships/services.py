from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from federation.activitypub import Audience
from memberships.models import SupporterLink, SupporterStatus, Tier


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


def normalize_email(email):
    return (email or "").strip().lower()


def resolve_tier(comic, name, link=None):
    if name:
        tier = Tier.objects.filter(comic=comic, name__iexact=name).first()
        if tier is not None:
            return tier
    if link is not None:
        latest = (
            SupporterStatus.objects.filter(link=link).order_by("-created_at").first()
        )
        if latest is not None:
            return latest.tier
    return None


def _apply_status(link, tier, valid_until):
    SupporterStatus.objects.filter(link=link, active=True).exclude(tier=tier).update(
        active=False
    )
    SupporterStatus.objects.update_or_create(
        link=link, tier=tier, defaults={"active": True, "valid_until": valid_until}
    )


def apply_supporter_entitlement(supporter):
    comic = supporter.connection.comic
    link = comic.supporter_links.filter(
        provider=supporter.connection.provider,
        provider_email__iexact=supporter.email,
    ).first()
    if link is None:
        return
    now = timezone.now()
    if (
        not supporter.active
        or supporter.valid_until is None
        or supporter.valid_until <= now
    ):
        SupporterStatus.objects.filter(link=link, active=True).update(active=False)
        return
    tier = resolve_tier(comic, supporter.tier_name, link)
    if tier is None:
        return
    _apply_status(link, tier, supporter.valid_until)
