from django.urls import reverse

from actors.models import Actor
from comics.models import Comic, Page, PageStatus
from memberships.services import can_view_page, page_requires_tier
from moderation import accounts, blocks
from moderation import services as moderation_services
from social.models import Follow, FollowStatus, Notification


def viewer_actor(user):
    if user is None or not user.is_authenticated:
        return None
    return Actor.objects.filter(user=user).first()


def followed_actor_ids(user):
    actor = viewer_actor(user)
    if actor is None:
        return []
    return list(
        Follow.objects.filter(
            follower=actor,
            status=FollowStatus.ACCEPTED,
            target__comic__isnull=False,
        ).values_list("target_id", flat=True)
    )


def page_visible(user, page, actor=None):
    if page.status != PageStatus.PUBLISHED:
        return False
    comic_actor = page.series.comic.actor
    if not accounts.publicly_visible(comic_actor):
        return False
    if blocks.is_blocked(user, comic_actor):
        return False
    if moderation_services.is_muted(user, comic_actor):
        return False
    if comic_actor.domain and moderation_services.is_domain_blocked(
        user, comic_actor.domain
    ):
        return False
    if moderation_services.is_filtered(user, actor=comic_actor, page=page):
        return False
    if actor is not None and not can_view_page(actor, page):
        return False
    if actor is None and page_requires_tier(page) is not None:
        return False
    return True


def visible_feed_pages(user):
    ids = followed_actor_ids(user)
    if not ids:
        return []
    actor = viewer_actor(user)
    pages = (
        Page.objects.filter(
            series__comic__actor_id__in=ids, status=PageStatus.PUBLISHED
        )
        .select_related("series__comic__actor", "series")
        .order_by("-published_at", "-created_at")
    )
    return [page for page in pages if page_visible(user, page, actor)]


def notification_visible(user, notification):
    actor = notification.actor
    if actor is not None and not accounts.publicly_visible(actor):
        return False
    page = notification.page
    if page is None and notification.comment_id:
        page = notification.comment.page
    if page is not None:
        if page.status != PageStatus.PUBLISHED:
            return False
        if not accounts.page_actor_visible(page):
            return False
    return moderation_services.notification_allowed(
        user, actor=actor, page=page, comment=notification.comment
    )


def visible_notifications(user):
    rows = (
        Notification.objects.filter(recipient=user)
        .select_related("actor", "page__series__comic", "comment__page__series__comic")
        .order_by("-created_at")
    )
    return [row for row in rows if notification_visible(user, row)]


def notification_target(notification):
    page = notification.page
    if page is None and notification.comment_id:
        page = notification.comment.page
    if page is not None:
        return reverse(
            "reader-page",
            args=[page.series.comic.slug, page.series.slug, page.position],
        )
    actor = notification.actor
    if actor is not None and actor.is_local:
        comic = Comic.objects.filter(actor=actor).first()
        if comic is not None:
            return reverse("comic-home", args=[comic.slug])
        return reverse("actor-detail", args=[actor.handle])
    return reverse("notifications")
