from django.core.exceptions import PermissionDenied
from django.db import transaction

from comics import permissions
from social.models import Comment, CommentBan, CommentStatus


def _require_moderator(user, comic):
    if not permissions.can_moderate(user, comic):
        raise PermissionDenied("You cannot moderate this comic's comments.")


def _set_status(user, comment, status):
    _require_moderator(user, comment.page.series.comic)
    comment.status = status
    comment.save(update_fields=["status", "updated_at"])
    return comment


def approve_comment(user, comment):
    return _set_status(user, comment, CommentStatus.VISIBLE)


def hide_comment(user, comment):
    return _set_status(user, comment, CommentStatus.HIDDEN)


def report_comment(user, comment):
    return _set_status(user, comment, CommentStatus.REPORTED)


@transaction.atomic
def ban_commenter(user, comic, actor):
    _require_moderator(user, comic)
    ban, _ = CommentBan.objects.get_or_create(comic=comic, actor=actor)
    Comment.objects.filter(
        page__series__comic=comic, actor=actor, status=CommentStatus.VISIBLE
    ).update(status=CommentStatus.HIDDEN, hidden_by_ban=True)
    return ban


@transaction.atomic
def unban_commenter(user, comic, actor):
    _require_moderator(user, comic)
    CommentBan.objects.filter(comic=comic, actor=actor).delete()
    Comment.objects.filter(
        page__series__comic=comic, actor=actor, hidden_by_ban=True
    ).update(status=CommentStatus.VISIBLE, hidden_by_ban=False)


def is_banned(comic, actor):
    return CommentBan.objects.filter(comic=comic, actor=actor).exists()
