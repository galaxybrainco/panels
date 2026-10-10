from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from comics.federation import federation_plan
from comics.models import Page, PageStatus
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


def federatable_page(object_id):
    page = Page.objects.filter(ap_id=object_id, status=PageStatus.PUBLISHED).first()
    if page is None or not federation_plan(page).emit:
        return None
    return page


def reply_target(value):
    if isinstance(value, dict):
        return value.get("id")
    return value


def store_inbound_reply(note, actor):
    if not isinstance(note, dict) or note.get("type") != "Note":
        return None
    ap_id = note.get("id")
    in_reply_to = reply_target(note.get("inReplyTo"))
    if not ap_id or not in_reply_to:
        return None
    if ap_id.startswith(settings.INSTANCE_URL):
        return None
    if Comment.objects.filter(ap_id=ap_id).exists():
        return None
    parent = None
    page = federatable_page(in_reply_to)
    if page is None:
        parent = Comment.objects.filter(ap_id=in_reply_to).first()
        if parent is None:
            return None
        page = parent.page
        if page.status != PageStatus.PUBLISHED or not federation_plan(page).emit:
            return None
    if is_banned(page.series.comic, actor):
        return None
    return Comment.objects.create(
        actor=actor,
        page=page,
        parent=parent,
        ap_id=ap_id,
        in_reply_to=in_reply_to,
        content=sanitize_html(note.get("content", "")),
        status=comment_status_for(page),
    )
