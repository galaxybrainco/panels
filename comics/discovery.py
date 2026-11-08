from django.core.exceptions import ValidationError
from django.db.models import Max, Q

from comics.models import Comic, ContentRating, Page, PageStatus
from federation.activitypub import Audience
from media.derivative_urls import DerivativeKind, derivative_url
from moderation import accounts
from moderation.reports import NotPermittedError, record_action


class InvalidRatingError(Exception):
    pass


def set_featured(comic, staff, featured):
    if staff is None or not staff.is_staff:
        raise NotPermittedError
    if not isinstance(featured, bool):
        raise ValidationError({"featured": "A boolean is required."})
    comic.featured = featured
    comic.save(update_fields=["featured", "updated_at"])
    record_action(
        staff,
        "comic.featured" if featured else "comic.unfeatured",
        target=f"comic:{comic.id}",
    )
    return comic


def _cover(comic):
    page = (
        Page.objects.filter(series__comic=comic, status=PageStatus.PUBLISHED)
        .exclude(audience__in=[Audience.MEMBERS, Audience.TIER])
        .prefetch_related("media")
        .order_by("-published_at", "-id")
        .first()
    )
    if page is None:
        return None
    media = next(iter(page.media.all()), None)
    return derivative_url(media, DerivativeKind.THUMBNAIL) if media else None


def discover_comics(*, q="", rating="all", tag=""):
    queryset = Comic.objects.filter(series__pages__status=PageStatus.PUBLISHED)
    ratings = {choice.value for choice in ContentRating}
    if rating and rating != "all":
        if rating not in ratings:
            raise InvalidRatingError
        queryset = queryset.filter(content_rating=rating)
    if tag:
        queryset = queryset.filter(tags__slug=tag)
    if q:
        queryset = queryset.filter(
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(tags__name__icontains=q)
            | Q(actor__handle__icontains=q)
        )
    queryset = (
        queryset.annotate(
            latest=Max(
                "series__pages__published_at",
                filter=Q(series__pages__status=PageStatus.PUBLISHED),
            )
        )
        .distinct()
        .order_by("-featured", "-latest", "title", "id")
    )
    return [comic for comic in queryset if accounts.publicly_visible(comic.actor)]


def cards(comics):
    result = []
    for comic in comics:
        result.append(
            {
                "comic": comic,
                "cover": _cover(comic),
                "tags": list(comic.tags.order_by("name")),
            }
        )
    return result
