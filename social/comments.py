from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from comics.models import PageStatus
from federation.activitypub import activity_context
from social.models import Comment, CommentStatus
from social.moderation import is_banned
from social.sanitize import sanitize_html


def comment_status_for(page):
    if page.series.comic.require_reply_approval:
        return CommentStatus.PENDING
    return CommentStatus.VISIBLE


@transaction.atomic
def add_comment(actor, page, content, parent=None):
    if page.status != PageStatus.PUBLISHED or not page.ap_id:
        raise ValidationError("Comments require a published page.")
    if is_banned(page.series.comic, actor):
        raise ValidationError("You are banned from replying to this comic.")
    if parent is not None and parent.page_id != page.id:
        raise ValidationError("The parent comment belongs to a different page.")
    comment = Comment(
        actor=actor,
        page=page,
        parent=parent,
        in_reply_to=parent.ap_id if parent else page.ap_id,
        content=sanitize_html(content),
        status=comment_status_for(page),
    )
    comment.ap_id = f"{settings.INSTANCE_URL}/comments/{comment.id}"
    comment.save()
    return comment


def comment_to_note(comment):
    comic = comment.page.series.comic
    target = comment.parent.actor.ap_id if comment.parent else comic.actor.ap_id
    return {
        "@context": activity_context(),
        "id": comment.ap_id,
        "type": "Note",
        "attributedTo": comment.actor.ap_id,
        "inReplyTo": comment.in_reply_to,
        "to": [target],
        "cc": [comic.actor.followers],
        "content": comment.content,
        "published": comment.created_at.isoformat(),
    }
