from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from actors.models import Actor, ActorType
from comics.services import create_comic, create_page, create_series
from social.comments import store_inbound_reply
from social.models import Comment
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


def scene():
    suffix = uuid4().hex[:8]
    owner = get_user_model().objects.create_user(
        email=f"owner-{suffix}@example.com", password="x"
    )
    comic = create_comic(owner, f"comic{suffix}", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)
    page.refresh_from_db()
    return owner, comic, page


def _note(ap_id, author, in_reply_to, content="<p>hi</p>"):
    return {
        "id": ap_id,
        "type": "Note",
        "attributedTo": author.ap_id,
        "inReplyTo": in_reply_to,
        "content": content,
    }


@pytest.mark.django_db
def test_store_inbound_reply_stores_reply_to_page():
    _, _, page = scene()
    bob = remote("bob")
    comment = store_inbound_reply(
        _note("https://bob.test/notes/1", bob, page.ap_id), bob
    )
    assert comment.page == page
    assert comment.parent is None
    assert comment.content == "<p>hi</p>"
    assert comment.activity_id == ""


@pytest.mark.django_db
def test_store_inbound_reply_is_idempotent():
    _, _, page = scene()
    bob = remote("bob")
    note = _note("https://bob.test/notes/1", bob, page.ap_id)
    store_inbound_reply(note, bob)
    assert store_inbound_reply(note, bob) is None
    assert Comment.objects.count() == 1


@pytest.mark.django_db
def test_store_inbound_reply_rejects_foreign_in_reply_to():
    bob = remote("bob")
    note = _note("https://bob.test/notes/1", bob, "https://elsewhere.test/notes/x")
    assert store_inbound_reply(note, bob) is None


@pytest.mark.django_db
def test_store_inbound_reply_rejects_local_note_id():
    _, _, page = scene()
    bob = remote("bob")
    note = _note("http://testserver/comments/spoof", bob, page.ap_id)
    assert store_inbound_reply(note, bob) is None
