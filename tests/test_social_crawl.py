from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django_tasks_db.models import DBTaskResult

from actors.models import Actor, ActorType
from comics.services import create_comic, create_page, create_series
from federation import handlers
from federation.models import Activity, ActivityDirection
from social import crawl
from social.comments import store_inbound_reply
from social.models import Comment
from social.tasks import crawl_comment_thread
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


@pytest.mark.django_db
def test_store_reply_resolves_author_and_stores(monkeypatch):
    _, _, page = scene()
    bob = remote("bob")
    note = _note("https://bob.test/notes/1", bob, page.ap_id)
    monkeypatch.setattr(
        "social.crawl.fetch_object", lambda url: note if url == note["id"] else None
    )
    comment = crawl.store_reply(note)
    assert comment is not None
    assert comment.actor == bob


@pytest.mark.django_db
def test_store_reply_drops_banned_author():
    owner, comic, page = scene()
    from social.moderation import ban_commenter

    bob = remote("bob")
    ban_commenter(owner, comic, bob)
    note = _note("https://bob.test/notes/1", bob, page.ap_id)
    assert crawl.store_reply(note) is None


@pytest.mark.django_db
def test_store_reply_drops_unresolvable_author(monkeypatch):
    _, _, page = scene()
    unknown = remote("ghost")
    note = _note("https://bob.test/notes/1", unknown, page.ap_id)
    monkeypatch.setattr("social.crawl.resolve_author", lambda note: None)
    assert crawl.store_reply(note) is None


@pytest.mark.django_db
def test_crawl_comment_stores_descendants(monkeypatch):
    _, _, page = scene()
    bob, carol = remote("bob"), remote("carol")
    parent = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>top</p>",
    )
    child = _note("https://carol.test/notes/2", carol, parent.ap_id)
    parent_note = {
        "id": parent.ap_id,
        "type": "Note",
        "attributedTo": bob.ap_id,
        "inReplyTo": page.ap_id,
        "content": "<p>top</p>",
        "replies": {"type": "Collection", "orderedItems": [child]},
    }
    documents = {parent.ap_id: parent_note, child["id"]: child}
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: documents.get(url))

    stored = crawl.crawl_comment(parent)

    assert stored == 1
    assert Comment.objects.filter(ap_id=child["id"], parent=parent).exists()
    parent.refresh_from_db()
    assert parent.crawled_at is not None


@pytest.mark.django_db
def test_crawl_comment_backfills_missing_ancestor(monkeypatch):
    _, _, page = scene()
    bob, carol = remote("bob"), remote("carol")
    stored_reply = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to="https://carol.test/notes/0",
        content="<p>reply</p>",
    )
    ancestor = _note("https://carol.test/notes/0", carol, page.ap_id)
    documents = {
        stored_reply.ap_id: {
            "id": stored_reply.ap_id,
            "type": "Note",
            "attributedTo": bob.ap_id,
            "inReplyTo": ancestor["id"],
            "content": "<p>reply</p>",
        },
        ancestor["id"]: ancestor,
    }
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: documents.get(url))

    crawl.crawl_comment(stored_reply)

    assert Comment.objects.filter(ap_id=ancestor["id"], page=page).exists()


@pytest.mark.django_db
def test_crawl_comment_respects_depth(monkeypatch):
    _, _, page = scene()
    bob = remote("bob")
    parent = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>top</p>",
    )
    documents = {}
    chain = [parent.ap_id]
    for index in range(4):
        ap_id = f"https://bob.test/notes/child-{index}"
        in_reply_to = chain[-1]
        author = remote(f"author{index}")
        documents[in_reply_to] = {
            "id": in_reply_to,
            "type": "Note",
            "attributedTo": bob.ap_id,
            "inReplyTo": page.ap_id if in_reply_to == parent.ap_id else "x",
            "content": "<p>x</p>",
            "replies": {
                "type": "Collection",
                "orderedItems": [_note(ap_id, author, in_reply_to)],
            },
        }
        chain.append(ap_id)
        documents[ap_id] = {
            "id": ap_id,
            "type": "Note",
            "attributedTo": author.ap_id,
            "inReplyTo": in_reply_to,
            "content": "<p>x</p>",
        }
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: documents.get(url))

    crawl.crawl_comment(parent)

    assert Comment.objects.filter(ap_id="https://bob.test/notes/child-1").exists()
    assert not Comment.objects.filter(ap_id="https://bob.test/notes/child-2").exists()


@pytest.mark.django_db
def test_inbound_create_enqueues_a_crawl():
    _, _, page = scene()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": _note("https://bob.test/notes/1", bob, page.ap_id),
    }
    activity = Activity.objects.create(
        ap_id=payload["id"],
        type="Create",
        actor=bob,
        direction=ActivityDirection.INBOUND,
        payload=payload,
    )
    handlers.dispatch(activity)
    assert Comment.objects.count() == 1
    assert DBTaskResult.objects.filter(
        task_path__contains="crawl_comment_thread"
    ).exists()


@pytest.mark.django_db
def test_crawl_command_enqueues_stale_comments():
    _, _, page = scene()
    bob = remote("bob")
    Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>x</p>",
    )
    call_command("crawl_comment_threads")
    assert (
        DBTaskResult.objects.filter(task_path__contains="crawl_comment_thread").count()
        == 1
    )


@pytest.mark.django_db
def test_crawl_task_runs(monkeypatch):
    _, _, page = scene()
    bob = remote("bob")
    comment = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>x</p>",
    )
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: None)
    crawl_comment_thread.func(str(comment.id))
    comment.refresh_from_db()
    assert comment.crawled_at is not None


@pytest.mark.django_db
def test_crawl_backfills_multi_level_ancestor_chain(monkeypatch):
    _, _, page = scene()
    bob, carol, dave = remote("bob"), remote("carol"), remote("dave")
    reply = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to="https://carol.test/notes/A",
        content="<p>r</p>",
    )
    note_a = _note("https://carol.test/notes/A", carol, "https://dave.test/notes/B")
    note_b = _note("https://dave.test/notes/B", dave, page.ap_id)
    docs = {
        reply.ap_id: {
            "id": reply.ap_id,
            "type": "Note",
            "attributedTo": bob.ap_id,
            "inReplyTo": note_a["id"],
            "content": "<p>r</p>",
        },
        note_a["id"]: note_a,
        note_b["id"]: note_b,
    }
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: docs.get(url))

    crawl.crawl_comment(reply)

    assert Comment.objects.filter(ap_id=note_b["id"], page=page).exists()
    assert Comment.objects.filter(
        ap_id=note_a["id"], parent__ap_id=note_b["id"]
    ).exists()


@pytest.mark.django_db
def test_inbound_create_records_activity_id():
    _, _, page = scene()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": _note("https://bob.test/notes/1", bob, page.ap_id),
    }
    activity = Activity.objects.create(
        ap_id=payload["id"],
        type="Create",
        actor=bob,
        direction=ActivityDirection.INBOUND,
        payload=payload,
    )
    handlers.dispatch(activity)
    assert Comment.objects.get(ap_id="https://bob.test/notes/1").activity_id == (
        "https://bob.test/activities/1"
    )


@pytest.mark.django_db
def test_resolve_author_handles_list_attributed_to():
    bob = remote("bob")
    note = {"attributedTo": [bob.ap_id]}
    assert crawl.resolve_author(note) == bob


@pytest.mark.django_db
def test_crawl_follows_paged_replies_collection(monkeypatch):
    _, _, page = scene()
    bob, carol = remote("bob"), remote("carol")
    parent = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>top</p>",
    )
    child = _note("https://carol.test/notes/2", carol, parent.ap_id)
    page_url = "https://bob.test/collections/1?page=1"
    docs = {
        parent.ap_id: {
            "id": parent.ap_id,
            "type": "Note",
            "attributedTo": bob.ap_id,
            "inReplyTo": page.ap_id,
            "content": "<p>top</p>",
            "replies": {"type": "OrderedCollection", "first": page_url},
        },
        page_url: {"type": "OrderedCollectionPage", "orderedItems": [child]},
        child["id"]: child,
    }
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: docs.get(url))

    crawl.crawl_comment(parent)

    assert Comment.objects.filter(ap_id=child["id"]).exists()


@pytest.mark.django_db
def test_crawl_budget_bounds_fetches(monkeypatch):
    _, _, page = scene()
    bob = remote("bob")
    parent = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>top</p>",
    )
    children = [
        _note(f"https://bob.test/notes/c{i}", bob, parent.ap_id) for i in range(50)
    ]
    docs = {
        parent.ap_id: {
            "id": parent.ap_id,
            "type": "Note",
            "attributedTo": bob.ap_id,
            "inReplyTo": page.ap_id,
            "content": "<p>top</p>",
            "replies": {"type": "Collection", "orderedItems": children},
        },
        **{child["id"]: child for child in children},
    }
    calls = {"n": 0}

    def fake(url):
        calls["n"] += 1
        return docs.get(url)

    monkeypatch.setattr("social.crawl.fetch_object", fake)

    crawl.crawl_comment(parent)

    assert calls["n"] <= crawl.MAX_NOTES + 2
