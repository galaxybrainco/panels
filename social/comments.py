from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from comics.models import PageStatus
from federation.activitypub import activity_context
from social.models import Comment, CommentStatus
from social.sanitize import sanitize_html


def comment_status_for(page):
    if page.series.comic.require_reply_approval:
        return CommentStatus.PENDING
    return CommentStatus.VISIBLE


@transaction.atomic
def add_comment(actor, page, content, parent=None):
    if page.status != PageStatus.PUBLISHED or not page.ap_id:
        raise ValidationError("Comments require a published page.")
    comment = Comment(
        actor=actor,
        page=page,
        parent=parent,
        ap_id=f"{settings.INSTANCE_URL}/comments/{uuid4()}",
        in_reply_to=parent.ap_id if parent else page.ap_id,
        content=sanitize_html(content),
        status=comment_status_for(page),
    )
    comment.save()
    return comment


def comment_to_note(comment):
    return {
        "@context": activity_context(),
        "id": comment.ap_id,
        "type": "Note",
        "attributedTo": comment.actor.ap_id,
        "inReplyTo": comment.in_reply_to,
        "content": comment.content,
        "published": comment.created_at.isoformat(),
    }
