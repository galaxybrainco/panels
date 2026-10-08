from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.models import ComicRole
from comics.services import create_comic, create_page, create_series
from social.comments import add_comment
from social.models import CommentBan, CommentStatus
from social.moderation import (
    approve_comment,
    ban_commenter,
    hide_comment,
    is_banned,
    report_comment,
    unban_commenter,
)
from tests.media_support import make_media


def remote(handle="bob"):
    domain = f"{handle}.test"
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
    )


def scene(require_reply_approval=False):
    suffix = uuid4().hex[:8]
    owner = get_user_model().objects.create_user(
        email=f"owner-{suffix}@example.com", password="x"
    )
    comic = create_comic(owner, f"comic{suffix}", "Lunar Baboon")
    if require_reply_approval:
        comic.require_reply_approval = True
        comic.save()
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)
    page.refresh_from_db()
    return owner, comic, page


@pytest.mark.django_db
def test_approve_makes_pending_visible():
    owner, _, page = scene(require_reply_approval=True)
    comment = add_comment(create_local_actor("alice"), page, "<p>x</p>")
    assert comment.status == CommentStatus.PENDING
    approve_comment(owner, comment)
    comment.refresh_from_db()
    assert comment.status == CommentStatus.VISIBLE


@pytest.mark.django_db
def test_hide_and_report():
    owner, _, page = scene()
    comment = add_comment(create_local_actor("alice"), page, "<p>x</p>")
    hide_comment(owner, comment)
    comment.refresh_from_db()
    assert comment.status == CommentStatus.HIDDEN
    report_comment(owner, comment)
    comment.refresh_from_db()
    assert comment.status == CommentStatus.REPORTED


@pytest.mark.django_db
def test_moderation_permissions():
    owner, comic, page = scene()
    contributor = get_user_model().objects.create_user(
        email="contributor@example.com", password="x"
    )
    outsider = get_user_model().objects.create_user(
        email="outsider@example.com", password="x"
    )
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    comment = add_comment(create_local_actor("alice"), page, "<p>x</p>")
    with pytest.raises(PermissionDenied):
        hide_comment(contributor, comment)
    with pytest.raises(PermissionDenied):
        hide_comment(outsider, comment)
    with pytest.raises(PermissionDenied):
        ban_commenter(contributor, comic, create_local_actor("mallory"))


@pytest.mark.django_db
def test_ban_hides_existing_comments_and_records_ban():
    owner, comic, page = scene()
    mallory = create_local_actor("mallory")
    comment = add_comment(mallory, page, "<p>x</p>")
    ban_commenter(owner, comic, mallory)
    comment.refresh_from_db()
    assert comment.status == CommentStatus.HIDDEN
    assert is_banned(comic, mallory) is True
    assert CommentBan.objects.filter(comic=comic, actor=mallory).exists()


@pytest.mark.django_db
def test_unban_removes_ban():
    owner, comic, _ = scene()
    mallory = create_local_actor("mallory")
    ban_commenter(owner, comic, mallory)
    unban_commenter(owner, comic, mallory)
    assert is_banned(comic, mallory) is False
