import json
from uuid import uuid4

import pytest
from django.urls import reverse

from actors.activitypub import actor_to_activitypub
from actors.models import Actor, ActorType
from comics.federation import page_note_id, page_to_note
from comics.models import Page
from social.comments import comment_to_note
from social.models import Comment
from tests.test_reader import _publish, _scene, _user


def _remote(handle="bob"):
    return Actor.objects.create(
        ap_id=f"https://remote.test/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain="remote.test",
    )


@pytest.mark.django_db
def test_page_note_has_context_and_search_consent():
    _, _, _, _, page, _, _ = _scene()
    _publish(page)
    page.refresh_from_db()
    note = page_to_note(page)
    assert note["context"] == page.series.comic.actor.outbox
    assert note["indexable"] is True
    Page.objects.filter(pk=page.pk).update(indexable=False)
    page.refresh_from_db()
    assert page_to_note(page)["indexable"] is False


@pytest.mark.django_db
def test_comment_note_uses_page_note_as_context():
    _, _, _, _, page, _, _ = _scene()
    _publish(page)
    page.refresh_from_db()
    comment = Comment.objects.create(
        actor=_remote(),
        page=page,
        ap_id=f"https://remote.test/comments/{uuid4()}",
        in_reply_to=page.ap_id,
        content="reply",
    )
    assert comment_to_note(comment)["context"] == page_note_id(page)
    assert comment_to_note(comment)["context"] == page.ap_id


@pytest.mark.django_db
def test_actor_document_exposes_fep_0ea0_payment_links():
    _, comic, _, _, _, _, _ = _scene()
    comic.payment_links = [
        {"name": "Ko-fi", "url": "https://ko-fi.com/example", "rel": "payment"}
    ]
    comic.save(update_fields=["payment_links", "updated_at"])
    document = actor_to_activitypub(comic.actor)
    assert document["attachment"] == [
        {
            "type": "Link",
            "href": "https://ko-fi.com/example",
            "name": "Ko-fi",
            "rel": "payment",
        }
    ]


@pytest.mark.django_db
def test_payment_links_endpoint_is_owner_only_and_validates(client):
    owner, comic, _, _, _, _, _ = _scene()
    url = reverse("comic-payment-links", args=[comic.slug])
    client.force_login(_user())
    assert (
        client.post(
            url,
            data='{"links": []}',
            content_type="application/json",
        ).status_code
        == 403
    )
    client.force_login(owner)
    response = client.post(
        url,
        data=json.dumps(
            {
                "links": [
                    {
                        "name": "Ko-fi",
                        "url": "https://ko-fi.com/example",
                        "rel": "payment",
                    }
                ]
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    comic.refresh_from_db()
    assert comic.payment_links[0]["name"] == "Ko-fi"
    bad = client.post(
        url,
        data=json.dumps({"links": [{"name": "Bad", "url": "not-a-url"}]}),
        content_type="application/json",
    )
    assert bad.status_code == 400
