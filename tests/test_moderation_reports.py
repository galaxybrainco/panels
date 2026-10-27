from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.models import ComicRole
from comics.services import create_comic, create_page, create_series
from federation.models import Activity
from moderation import blocks as block_services
from moderation import reports
from moderation.models import (
    ModerationAudit,
    ModerationNote,
    Report,
    ReportCategory,
    ReportStatus,
)


def _user(staff=False):
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com",
        password="x",
        is_staff=staff,
    )


def _remote(handle="bob", domain="remote.test"):
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
        shared_inbox=f"https://{domain}/inbox",
    )


def _scene():
    owner = _user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series, title="The Landing")
    page.ap_id = f"https://testserver/pages/{page.id}"
    page.save(update_fields=["ap_id", "updated_at"])
    comment = page.comments.create(
        actor=_remote(),
        ap_id=f"https://remote.test/comments/{uuid4()}",
        in_reply_to=f"https://testserver/pages/{page.id}",
        content="spam here",
    )
    return owner, comic, page, comment


@pytest.mark.django_db
def test_report_requires_exactly_one_target_and_reporter():
    user = _user()
    actor = _remote()
    with pytest.raises(IntegrityError), transaction.atomic():
        Report.objects.create(
            reporter_user=user, reporter_actor=actor, target_actor=actor
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Report.objects.create(reporter_user=user, reason="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        Report.objects.create(reporter_actor=actor, reason="x")


@pytest.mark.django_db
def test_report_creation_writes_audit():
    user = _user()
    actor = _remote()
    report = reports.report_target(
        user, target_type="actor", target_id=actor.id, reason="harassment"
    )
    assert report.status == ReportStatus.OPEN
    assert report.category == ReportCategory.OTHER
    audit = ModerationAudit.objects.get(action="report.created")
    assert audit.actor == user
    assert audit.target == f"report:{report.id}"


@pytest.mark.django_db
def test_open_report_is_deduped_per_reporter_and_target():
    user = _user()
    actor = _remote()
    first = reports.report_target(
        user, target_type="actor", target_id=actor.id, reason="one"
    )
    second = reports.report_target(
        user,
        target_type="actor",
        target_id=actor.id,
        reason="two",
        category=ReportCategory.SPAM,
    )
    assert first.pk == second.pk
    assert Report.objects.count() == 1
    second.refresh_from_db()
    assert second.reason == "two"
    assert second.category == ReportCategory.SPAM
    assert ModerationAudit.objects.filter(action="report.created").count() == 1


@pytest.mark.django_db
def test_report_validation():
    user = _user()
    actor = _remote()
    with pytest.raises(reports.InvalidTargetError):
        reports.report_target(
            user, target_type="nonsense", target_id=actor.id, reason="x"
        )
    with pytest.raises(reports.InvalidTargetError):
        reports.report_target(user, target_type="actor", target_id=uuid4(), reason="x")
    with pytest.raises(reports.InvalidReportError):
        reports.report_target(
            user, target_type="actor", target_id=actor.id, reason="   "
        )
    with pytest.raises(reports.InvalidReportError):
        reports.report_target(
            user,
            target_type="actor",
            target_id=actor.id,
            reason="x",
            category="nope",
        )
    with pytest.raises(reports.InvalidReportError):
        reports.report_target(user, target_type="actor", target_id=actor.id, reason=123)


@pytest.mark.django_db
def test_scoping_staff_and_comic_moderator():
    owner, comic, page, comment = _scene()
    editor = _user()
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.MODERATOR)
    outsider = _user()
    staff = _user(staff=True)
    actor_report = reports.report_target(
        owner, target_type="actor", target_id=_remote(handle="reported").id, reason="x"
    )
    page_report = reports.report_target(
        owner, target_type="page", target_id=page.id, reason="x"
    )
    comment_report = reports.report_target(
        owner, target_type="comment", target_id=comment.id, reason="x"
    )

    assert reports.can_view(staff, actor_report) is True
    assert reports.can_view(owner, actor_report) is False
    assert reports.can_view(owner, page_report) is True
    assert reports.can_view(editor, comment_report) is True
    assert reports.can_view(outsider, page_report) is False
    visible = set(reports.visible_reports(editor).values_list("pk", flat=True))
    assert visible == {page_report.pk, comment_report.pk}


@pytest.mark.django_db
def test_notes_are_staff_only_and_audited():
    actor = _remote()
    staff = _user(staff=True)
    note = reports.add_note(staff, actor, "watching")
    assert ModerationNote.objects.filter(pk=note.pk).exists()
    assert ModerationAudit.objects.get(action="note.created").actor == staff
    with pytest.raises(reports.NotPermittedError):
        reports.add_note(_user(), actor, "nope")
    with pytest.raises(reports.NotPermittedError):
        list(reports.notes_for(_user(), actor))


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "moderation.reports.fan_out",
        lambda payload, inboxes, actor: calls.append((payload, inboxes)),
        raising=False,
    )
    return calls


def _activity(actor, payload):
    return Activity.objects.create(
        ap_id=payload["id"],
        type=payload.get("type", ""),
        actor=actor,
        payload=payload,
    )


@pytest.mark.django_db
def test_forwarding_remote_actor_emits_flag(sent):
    user = _user()
    create_local_actor(f"u{uuid4().hex[:8]}", user=user)
    actor = _remote()
    report = reports.report_target(
        user, target_type="actor", target_id=actor.id, reason="abuse", forward=True
    )
    report.refresh_from_db()
    assert report.forwarded_at is not None
    assert len(sent) == 1
    payload, inboxes = sent[0]
    assert payload["type"] == "Flag"
    assert payload["object"] == actor.ap_id
    assert payload["content"] == "other: abuse"
    assert inboxes == ["https://remote.test/inbox"]
    assert ModerationAudit.objects.filter(action="report.forwarded").count() == 1


@pytest.mark.django_db
def test_forwarding_requires_personal_actor(sent):
    user = _user()
    actor = _remote()
    report = reports.report_target(
        user, target_type="actor", target_id=actor.id, reason="x"
    )
    with pytest.raises(block_services.NoActorError):
        reports.forward_report(report)
    report.refresh_from_db()
    assert report.forwarded_at is None


@pytest.mark.django_db
def test_forwarding_local_target_is_rejected(sent):
    user = _user()
    create_local_actor(f"u{uuid4().hex[:8]}", user=user)
    _, _, page, _ = _scene()
    report = reports.report_target(
        user, target_type="page", target_id=page.id, reason="x"
    )
    with pytest.raises(reports.NotForwardableError):
        reports.forward_report(report)


@pytest.mark.django_db
def test_forwarding_remote_comment_uses_comment_author(sent):
    user = _user()
    create_local_actor(f"u{uuid4().hex[:8]}", user=user)
    _, _, _, comment = _scene()
    reports.report_target(
        user, target_type="comment", target_id=comment.id, reason="x", forward=True
    )
    payload, inboxes = sent[0]
    assert payload["object"] == comment.ap_id
    assert inboxes == ["https://remote.test/inbox"]


@pytest.mark.django_db
def test_inbound_flag_stores_and_dedupes():
    _, _, page, _ = _scene()
    remote = _remote(handle="reporter")
    payload = {
        "id": "https://remote.test/flags/1",
        "type": "Flag",
        "actor": remote.ap_id,
        "object": page.ap_id,
        "content": "spam",
    }
    activity = _activity(remote, payload)
    data = reports.record_inbound_flag(activity)
    assert data.reporter_actor == remote
    assert data.target_page == page
    assert data.remote_activity_id == payload["id"]
    again = reports.record_inbound_flag(activity)
    assert again.pk == data.pk
    assert Report.objects.count() == 1


@pytest.mark.django_db
def test_inbound_flag_unknown_target_is_ignored():
    remote = _remote(handle="reporter")
    payload = {
        "id": "https://remote.test/flags/2",
        "type": "Flag",
        "actor": remote.ap_id,
        "object": "https://elsewhere.test/actors/nobody",
        "content": "spam",
    }
    assert reports.record_inbound_flag(_activity(remote, payload)) is None
    assert Report.objects.count() == 0
