from comics.models import ComicRole
from social.models import Follow, FollowStatus, Notification, NotificationType


def _actor_user(actor):
    if actor is not None and actor.user_id:
        return actor.user
    return None


def notify(recipient, *, kind, actor=None, page=None, comment=None):
    if recipient is None:
        return None
    actor_user = _actor_user(actor)
    if actor_user is not None and actor_user.pk == recipient.pk:
        return None
    return Notification.objects.create(
        recipient=recipient, type=kind, actor=actor, page=page, comment=comment
    )


def mark_read(notification):
    if not notification.read:
        notification.read = True
        notification.save(update_fields=["read", "updated_at"])
    return notification


def mark_all_read(user):
    return Notification.objects.filter(recipient=user, read=False).update(read=True)


def notify_new_page(page):
    comic = page.series.comic
    followers = (
        Follow.objects.filter(
            target=comic.actor,
            status=FollowStatus.ACCEPTED,
            follower__domain="",
            follower__user__isnull=False,
        )
        .select_related("follower__user")
        .order_by("created_at")
    )
    created = []
    for follow in followers:
        recipient = follow.follower.user
        if recipient.pk == page.published_by_id:
            continue
        note = notify(
            recipient, kind=NotificationType.NEW_PAGE, actor=comic.actor, page=page
        )
        if note is not None:
            created.append(note)
    return created


def notify_follow(follow):
    target = follow.target
    comic = getattr(target, "comic", None)
    if comic is not None:
        owner_role = (
            comic.roles.filter(role=ComicRole.Role.OWNER).select_related("user").first()
        )
        recipient = owner_role.user if owner_role is not None else None
    else:
        recipient = _actor_user(target)
    return notify(recipient, kind=NotificationType.FOLLOW, actor=follow.follower)
