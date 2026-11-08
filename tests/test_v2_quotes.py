from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.urls import reverse

from actors.models import Actor, ActorType
from comics import quotes
from comics.models import (
    FederationMode,
    Page,
    QuotePolicy,
    QuoteRequest,
    QuoteStatus,
)
from moderation.models import ModerationAudit
from social.handlers import handle_create
from tests.test_reader import _publish, _scene, _user


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        quotes,
        "fan_out",
        lambda payload, inboxes, actor: calls.append((payload, inboxes)),
    )
    return calls


def _remote(handle="quoter"):
    return Actor.objects.create(
        ap_id=f"https://remote.test/users/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain="remote.test",
        inbox=f"https://remote.test/users/{handle}/inbox",
        shared_inbox="https://remote.test/inbox",
    )


def _activity(actor, page, quote_id="https://remote.test/notes/quote-1", **note):
    payload = {
        "id": f"https://remote.test/activities/{uuid4()}",
        "type": "Create",
        "actor": actor.ap_id,
        "object": {
            "id": quote_id,
            "type": "Note",
            "attributedTo": actor.ap_id,
            "content": "<p>quoting</p>",
            "tag": [
                {
                    "type": "Link",
                    "href": page.ap_id,
                    "mediaType": 'application/ld+json; profile="https://www.w3.org/ns/activitystreams"',
                    "name": f"RE: {page.ap_id}",
                }
            ],
            **note,
        },
    }
    return SimpleNamespace(ap_id=payload["id"], actor=actor, payload=payload)


def _page():
    owner, comic, _, _, page, _, _ = _scene()
    _publish(page)
    page.refresh_from_db()
    return owner, comic, page


@pytest.mark.django_db
def test_manual_quote_waits_for_owner(sent):
    _, _, page = _page()
    remote = _remote()
    request = quotes.process_inbound_quote(_activity(remote, page))
    assert request.status == QuoteStatus.PENDING
    assert request.page == page
    assert sent == []


@pytest.mark.django_db
def test_allow_policy_auto_approves_with_authorization(sent, client):
    _, _, page = _page()
    Page.objects.filter(pk=page.pk).update(quote_policy=QuotePolicy.ALLOW)
    page.refresh_from_db()
    remote = _remote()
    request = quotes.process_inbound_quote(_activity(remote, page))
    assert request.status == QuoteStatus.APPROVED
    payload, inboxes = sent[0]
    assert payload["type"] == "Accept"
    assert payload["object"]["type"] == "QuoteAuthorization"
    assert payload["object"]["interactingObject"] == request.quote_id
    assert inboxes == ["https://remote.test/inbox"]

    key = request.authorization_id.rsplit("/", 1)[-1]
    response = client.get(reverse("quote-authorization", args=[key]))
    assert response.status_code == 200
    assert response.json()["type"] == "QuoteAuthorization"


@pytest.mark.django_db
def test_deny_policy_rejects_and_duplicates_are_ignored(sent):
    _, _, page = _page()
    Page.objects.filter(pk=page.pk).update(quote_policy=QuotePolicy.DENY)
    page.refresh_from_db()
    remote = _remote()
    activity = _activity(remote, page)
    first = quotes.process_inbound_quote(activity)
    assert first.status == QuoteStatus.REJECTED
    assert sent[0][0]["type"] == "Reject"
    quotes.process_inbound_quote(activity)
    assert QuoteRequest.objects.count() == 1
    assert len(sent) == 1


@pytest.mark.django_db
def test_quote_property_and_handler_integration(sent):
    _, _, page = _page()
    remote = _remote()
    activity = _activity(remote, page, quote=page.ap_id)
    activity.payload["object"].pop("tag")
    handle_create(activity)
    assert QuoteRequest.objects.get().page == page


@pytest.mark.django_db
def test_local_only_pages_are_not_quotable(sent):
    _, _, page = _page()
    Page.objects.filter(pk=page.pk).update(federation=FederationMode.LOCAL_ONLY)
    page.refresh_from_db()
    assert quotes.process_inbound_quote(_activity(_remote(), page)) is None
    assert QuoteRequest.objects.count() == 0


@pytest.mark.django_db
def test_owner_queue_approve_reject_and_audit(client, sent):
    owner, comic, page = _page()
    remote = _remote()
    request = quotes.process_inbound_quote(_activity(remote, page))
    client.force_login(_user())
    assert client.get(reverse("comic-quotes", args=[comic.slug])).status_code == 403
    client.force_login(owner)
    listing = client.get(reverse("comic-quotes", args=[comic.slug]))
    assert listing.json()["quotes"][0]["id"] == str(request.id)
    approve = client.post(
        reverse("comic-quote-decision", args=[comic.slug, request.id]),
        data='{"decision": "approve"}',
        content_type="application/json",
    )
    assert approve.status_code == 200
    request.refresh_from_db()
    assert request.status == QuoteStatus.APPROVED
    assert ModerationAudit.objects.filter(action="quote.approved").exists()
    again = client.post(
        reverse("comic-quote-decision", args=[comic.slug, request.id]),
        data='{"decision": "reject"}',
        content_type="application/json",
    )
    assert again.status_code == 409


@pytest.mark.django_db
def test_quote_policy_endpoint_owner_only(client):
    owner, comic, page = _page()
    url = reverse(
        "comic-quote-policy",
        args=[comic.slug, page.series.slug, page.position],
    )
    client.force_login(_user())
    assert (
        client.post(
            url, data='{"policy": "allow"}', content_type="application/json"
        ).status_code
        == 403
    )
    client.force_login(owner)
    response = client.post(
        url, data='{"policy": "allow"}', content_type="application/json"
    )
    assert response.status_code == 200
    page.refresh_from_db()
    assert page.quote_policy == QuotePolicy.ALLOW
    assert (
        client.post(
            url, data='{"policy": "bogus"}', content_type="application/json"
        ).status_code
        == 400
    )
