from social.models import Notification


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
