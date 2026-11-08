from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.federation import page_to_note
from comics.models import Page, ReplyPolicy
from federation.activitypub import PUBLIC
from moderation.models import ModerationAudit
from social.comments import add_comment, store_inbound_reply
from social.models import CommentStatus, Follow, FollowStatus
from tests.test_reader import _publish, _scene, _user


def _remote(handle="bob"):
    return Actor.objects.create(
        ap_id=f"https://remote.test/users/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain="remote.test",
        inbox=f"https://remote.test/users/{handle}/inbox",
    )


def _page(policy=ReplyPolicy.EVERYONE):
    _, comic, _, _, page, _, _ = _scene()
    _publish(page)
    page.refresh_from_db()
    Page.objects.filter(pk=page.pk).update(reply_policy=policy)
    page.refresh_from_db()
    return comic, page


def _note(page, remote, quote=None):
    return {
        "id": f"https://remote.test/notes/{uuid4()}",
        "type": "Note",
        "attributedTo": remote.ap_id,
        "inReplyTo": page.ap_id,
        "content": "<p>reply</p>",
    }


@pytest.mark.django_db
def test_interaction_policy_serialization():
    comic, page = _page(ReplyPolicy.EVERYONE)
    policy = page_to_note(page)["interactionPolicy"]["canReply"]
    assert policy["automaticApproval"] == [PUBLIC]
    assert policy["manualApproval"] == []

    Page.objects.filter(pk=page.pk).update(reply_policy=ReplyPolicy.FOLLOWERS)
    page.refresh_from_db()
    policy = page_to_note(page)["interactionPolicy"]["canReply"]
    assert policy["automaticApproval"] == [comic.actor.followers]

    Page.objects.filter(pk=page.pk).update(reply_policy=ReplyPolicy.NOBODY)
    page.refresh_from_db()
    policy = page_to_note(page)["interactionPolicy"]["canReply"]
    assert policy["automaticApproval"] == []
    assert policy["manualApproval"] == []

    Page.objects.filter(pk=page.pk).update(reply_policy=ReplyPolicy.EVERYONE)
    comic.require_reply_approval = True
    comic.save(update_fields=["require_reply_approval"])
    page.refresh_from_db()
    policy = page_to_note(page)["interactionPolicy"]["canReply"]
    assert policy["automaticApproval"] == []
    assert policy["manualApproval"] == [PUBLIC]


@pytest.mark.django_db
def test_followers_only_inbound_replies_require_follow():
    comic, page = _page(ReplyPolicy.FOLLOWERS)
    remote = _remote()
    assert store_inbound_reply(_note(page, remote), remote) is None
    Follow.objects.create(
        follower=remote, target=comic.actor, status=FollowStatus.ACCEPTED
    )
    comment = store_inbound_reply(_note(page, remote), remote)
    assert comment is not None
    assert comment.status == CommentStatus.VISIBLE


@pytest.mark.django_db
def test_nobody_blocks_inbound_and_local_replies():
    comic, page = _page(ReplyPolicy.NOBODY)
    remote = _remote()
    assert store_inbound_reply(_note(page, remote), remote) is None
    owner = comic.roles.get(role="owner").user
    actor = create_local_actor(f"owner{uuid4().hex[:6]}", user=owner)
    assert add_comment(actor, page, "owner reply") is not None

    outsider = create_local_actor(f"u{uuid4().hex[:6]}", user=_user())
    with pytest.raises(ValidationError):
        add_comment(outsider, page, "nope")


@pytest.mark.django_db
def test_approval_still_marks_pending_for_allowed_replies():
    comic, page = _page(ReplyPolicy.EVERYONE)
    comic.require_reply_approval = True
    comic.save(update_fields=["require_reply_approval"])
    comment = store_inbound_reply(_note(page, _remote()), _remote(handle="fan"))
    assert comment.status == CommentStatus.PENDING


@pytest.mark.django_db
def test_reply_policy_endpoint_is_owner_only_and_audited(client):
    owner, comic, _, _, page, _, _ = _scene()
    _publish(page)
    page.refresh_from_db()
    url = reverse(
        "comic-reply-policy", args=[comic.slug, page.series.slug, page.position]
    )
    client.force_login(_user())
    assert (
        client.post(
            url, data='{"policy": "followers"}', content_type="application/json"
        ).status_code
        == 403
    )
    client.force_login(owner)
    response = client.post(
        url, data='{"policy": "followers"}', content_type="application/json"
    )
    assert response.status_code == 200
    page.refresh_from_db()
    assert page.reply_policy == ReplyPolicy.FOLLOWERS
    assert ModerationAudit.objects.filter(action="page.reply_policy_changed").exists()
    assert (
        client.post(
            url, data='{"policy": "bogus"}', content_type="application/json"
        ).status_code
        == 400
    )
