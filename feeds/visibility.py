from comics.models import Comic, Page, PageStatus, Series
from federation.activitypub import Audience

PUBLIC_AUDIENCES = [Audience.PUBLIC, Audience.UNLISTED]
HIDDEN_STATES = ["limited", "suspended", "disabled"]


def visible_comics():
    return Comic.objects.exclude(actor__moderation__state__in=HIDDEN_STATES)


def public_pages(comic):
    return (
        Page.objects.filter(
            series__comic=comic,
            status=PageStatus.PUBLISHED,
            audience__in=PUBLIC_AUDIENCES,
        )
        .select_related("series__comic", "series")
        .prefetch_related("media")
        .order_by("-published_at", "-id")
    )


def public_page_qs():
    return (
        Page.objects.filter(
            series__comic__in=visible_comics(),
            status=PageStatus.PUBLISHED,
            audience__in=PUBLIC_AUDIENCES,
        )
        .select_related("series__comic", "series")
        .prefetch_related("media")
        .order_by("-published_at", "-id")
    )


def public_series():
    return (
        Series.objects.filter(comic__in=visible_comics(), pages__in=public_page_qs())
        .distinct()
        .order_by("comic__title", "position", "id")
    )
