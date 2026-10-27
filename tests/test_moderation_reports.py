from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from actors.models import Actor, ActorType
from comics.models import ComicRole
from comics.services import create_comic, create_page, create_series
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
