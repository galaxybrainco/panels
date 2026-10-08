import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from federation import handlers
from federation.activitypub import Audience
from federation.models import Activity, ActivityDirection
from social.comments import add_comment, comment_to_note
from social.models import Comment, CommentStatus
from social.sanitize import sanitize_html
from tests.media_support import make_media


def local(handle="alice"):
    return create_local_actor(handle)


def remote(handle="bob"):
    domain = f"{handle}.test"
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
    )


def published_page(require_reply_approval=False):
    from uuid import uuid4

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
    return comic, page


def test_sanitize_html_strips_scripts_and_handlers():
    assert sanitize_html("<p>hi</p><script>bad()</script>") == "<p>hi</p>"
    cleaned = sanitize_html('<a href="https://a.test" onclick="x()">l</a>')
    assert 'href="https://a.test"' in cleaned
    assert "onclick" not in cleaned
    assert 'rel="noopener noreferrer"' in cleaned


@pytest.mark.django_db
def test_add_comment_sanitizes_and_links_to_page():
    _, page = published_page()
    comment = add_comment(local("alice"), page, "<p>Nice</p><script>x()</script>")
    assert comment.content == "<p>Nice</p>"
    assert comment.in_reply_to == page.ap_id
    assert comment.ap_id.startswith("http://testserver/comments/")
    assert comment.status == CommentStatus.VISIBLE


@pytest.mark.django_db
def test_add_comment_reply_links_parent():
    _, page = published_page()
    top = add_comment(local("alice"), page, "<p>Top</p>")
    reply = add_comment(local("bob"), page, "<p>Reply</p>", parent=top)
    assert reply.parent == top
    assert reply.in_reply_to == top.ap_id
    assert reply.page == page


@pytest.mark.django_db
def test_add_comment_pending_when_comic_requires_approval():
    _, page = published_page(require_reply_approval=True)
    comment = add_comment(local("alice"), page, "<p>x</p>")
    assert comment.status == CommentStatus.PENDING


@pytest.mark.django_db
def test_add_comment_requires_a_published_page():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    draft = create_page(owner, series)
    with pytest.raises(ValidationError):
        add_comment(local("alice"), draft, "<p>x</p>")


@pytest.mark.django_db
def test_comment_to_note_shape():
    _, page = published_page()
    comment = add_comment(local("alice"), page, "<p>Nice</p>")
    note = comment_to_note(comment)
    assert note["type"] == "Note"
    assert note["id"] == comment.ap_id
    assert note["attributedTo"] == comment.actor.ap_id
    assert note["inReplyTo"] == page.ap_id
    assert note["content"] == "<p>Nice</p>"
    assert "published" in note


def _inbound(activity_type, actor, payload):
    return Activity.objects.create(
        ap_id=payload["id"],
        type=activity_type,
        actor=actor,
        direction=ActivityDirection.INBOUND,
        payload=payload,
    )


@pytest.mark.django_db
def test_inbound_create_reply_to_page():
    _, page = published_page()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "https://bob.test/notes/1",
            "type": "Note",
            "inReplyTo": page.ap_id,
            "content": "<p>Great page</p><script>x()</script>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    comment = Comment.objects.get(ap_id="https://bob.test/notes/1")
    assert comment.page == page
    assert comment.parent is None
    assert comment.content == "<p>Great page</p>"
    assert comment.status == CommentStatus.VISIBLE


@pytest.mark.django_db
def test_inbound_create_reply_to_comment_links_parent():
    _, page = published_page()
    parent = add_comment(local("alice"), page, "<p>Top</p>")
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "https://bob.test/notes/1",
            "type": "Note",
            "inReplyTo": parent.ap_id,
            "content": "<p>Reply</p>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    comment = Comment.objects.get(ap_id="https://bob.test/notes/1")
    assert comment.parent == parent
    assert comment.page == page


@pytest.mark.django_db
def test_inbound_create_ignored_for_foreign_in_reply_to():
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "https://bob.test/notes/1",
            "type": "Note",
            "inReplyTo": "https://elsewhere.test/notes/nope",
            "content": "<p>x</p>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    assert not Comment.objects.exists()


@pytest.mark.django_db
def test_inbound_create_ignored_for_members_page():
    _, page = published_page()
    page.audience = Audience.MEMBERS
    page.save()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "https://bob.test/notes/1",
            "type": "Note",
            "inReplyTo": page.ap_id,
            "content": "<p>x</p>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    assert not Comment.objects.exists()


@pytest.mark.django_db
def test_inbound_create_ignored_for_non_note():
    _, page = published_page()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {"id": page.ap_id, "type": "Article", "inReplyTo": page.ap_id},
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    assert not Comment.objects.exists()


@pytest.mark.django_db
def test_inbound_create_is_idempotent():
    _, page = published_page()
    bob = remote("bob")
    first = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "https://bob.test/notes/1",
            "type": "Note",
            "inReplyTo": page.ap_id,
            "content": "<p>x</p>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, first))
    handlers.dispatch(_inbound("Create", bob, {**first, "id": "https://bob.test/a/2"}))
    assert Comment.objects.count() == 1


@pytest.mark.django_db
def test_comment_endpoint_serves_visible_local_comment(client):
    _, page = published_page()
    comment = add_comment(local("alice"), page, "<p>Nice</p>")
    response = client.get(f"/comments/{comment.id}")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/activity+json")
    assert response.json()["type"] == "Note"
    assert response.json()["id"] == comment.ap_id


@pytest.mark.django_db
def test_comment_endpoint_404_for_pending_comment(client):
    _, page = published_page(require_reply_approval=True)
    comment = add_comment(local("alice"), page, "<p>Nice</p>")
    assert comment.status == CommentStatus.PENDING
    assert client.get(f"/comments/{comment.id}").status_code == 404


@pytest.mark.django_db
def test_comment_endpoint_404_for_remote_comment(client):
    _, page = published_page()
    bob = remote("bob")
    comment = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>x</p>",
    )
    assert client.get(f"/comments/{comment.id}").status_code == 404


@pytest.mark.django_db
def test_comment_note_id_is_dereferenceable(client):
    from urllib.parse import urlparse

    _, page = published_page()
    comment = add_comment(local("alice"), page, "<p>Nice</p>")
    path = urlparse(comment_to_note(comment)["id"]).path
    assert client.get(path).status_code == 200


@pytest.mark.django_db
def test_comment_endpoint_404_when_page_gated(client):
    _, page = published_page()
    comment = add_comment(local("alice"), page, "<p>Nice</p>")
    page.audience = Audience.MEMBERS
    page.save()
    assert client.get(f"/comments/{comment.id}").status_code == 404


@pytest.mark.django_db
def test_add_comment_rejects_parent_from_another_page():
    _, page_a = published_page()
    _, page_b = published_page()
    parent = add_comment(local("alice"), page_a, "<p>Top</p>")
    with pytest.raises(ValidationError):
        add_comment(local("bob"), page_b, "<p>Reply</p>", parent=parent)


@pytest.mark.django_db
def test_comment_to_note_has_addressing():
    _, page = published_page()
    comment = add_comment(local("alice"), page, "<p>Nice</p>")
    note = comment_to_note(comment)
    assert note["to"] == [page.series.comic.actor.ap_id]
    assert note["cc"] == [page.series.comic.actor.followers]


@pytest.mark.django_db
def test_inbound_create_ignored_when_note_id_is_local():
    _, page = published_page()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "http://testserver/comments/spoofed",
            "type": "Note",
            "inReplyTo": page.ap_id,
            "content": "<p>x</p>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    assert not Comment.objects.exists()
