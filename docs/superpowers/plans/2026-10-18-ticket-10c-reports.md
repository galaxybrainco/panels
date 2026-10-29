# Reports, Notes, and Audit (10c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local reports and inbound `Flag`s share one scoped queue; moderators assign, resolve/reject, forward, hide/approve/ban from a report; account notes are staff-only; every action is audited.

**Architecture:** `moderation.Report`, `ModerationNote`, and `ModerationAudit` store the workflow. `moderation/reports.py` owns creation/dedupe, forwarding (outbound `Flag` through `build_activity` + `fan_out`), scoping, transitions, inline comment actions, notes, and audit rows. A `Flag` handler feeds inbound reports into the same model; JSON endpoints expose the queue.

**Tech Stack:** Python 3.14, Django 6.1.1, pytest, `monkeypatch` for delivery capture.

**Spec:** `docs/superpowers/specs/2026-10-18-reports-moderation-design.md`

## Global Constraints

- Exactly one target and one reporter per `Report`; enforce with DB check constraints.
- Instance staff (`is_staff`) see all reports/notes/audit; comic moderators only reports targeting their comic.
- Every report creation/assignment/status change/forward and every note writes exactly one audit row; audit is append-only (no update/delete surface, no admin registration).
- Forwarding only for remote offending actors (account targets or remote comment authors); never for local targets.
- Outbound `Flag` uses the reporter's personal actor; no actor → 409 `no_actor`, report stays open.
- Inbound `Flag` dedupes by `remote_activity_id`; unknown targets are acknowledged but not stored; `reject_reports` behavior is unchanged.
- Keep `ruff check .` and `ruff format --check .` clean; run `uv run python manage.py makemigrations --check --dry-run` before final commits.

## Review Focus

- **Constraint integrity:** exactly-one target/reporter, self-targets, unknown UUIDs, inbound dedupe uniqueness.
- **Scoping leaks:** invisible reports must be 404 (not 403), staff-only assignment/notes/audit, comic moderators can act only on their targets.
- **Forwarding correctness:** object/destination for account vs comment targets, no forwarding of local targets or inbound reports, no-actor retries.
- **Audit completeness:** one row per action with the right action name/target, no duplicate rows on dedupe updates, staff-only listing.
- **Transition safety:** closed reports cannot be re-resolved, invalid statuses 400, resolution text required.

---

### Task 1: Models, audit, scoping, local report creation

**Files:**
- Modify: `moderation/models.py`
- Create: `moderation/migrations/0003_reports.py` (generated)
- Create: `moderation/reports.py`
- Create: `tests/test_moderation_reports.py`

**Interfaces:**
- Consumes: `Actor`, `Page`, `Comment`, `Comic`, `permissions.can_moderate`.
- Produces: `Report`, `ReportCategory`, `ReportStatus`, `ModerationNote`, `ModerationAudit`; `reports.can_view`, `visible_reports`, `target_comic`, `record_action`, `report_target`, `add_note`, `notes_for`, `audit_for`; errors `InvalidTargetError`, `InvalidReportError`, `NotPermittedError`, `NotForwardableError`, `ReportClosedError`.

- [ ] **Step 1: Write the failing model/service tests**

Create `tests/test_moderation_reports.py`:

```python
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from actors.models import Actor, ActorType
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
        reports.report_target(
            user, target_type="actor", target_id=uuid4(), reason="x"
        )
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


@pytest.mark.django_db
def test_scoping_staff_and_comic_moderator():
    owner, comic, page, comment = _scene()
    editor = _user()
    from comics.models import ComicRole

    ComicRole.objects.create(
        comic=comic, user=editor, role=ComicRole.Role.MODERATOR
    )
    outsider = _user()
    staff = _user(staff=True)
    actor_report = reports.report_target(
        owner, target_type="actor", target_id=_remote().id, reason="x"
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
    assert visible == {comment_report.pk}


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_moderation_reports.py -q`
Expected: `ImportError: cannot import name 'reports'`.

- [ ] **Step 3: Implement the models**

Append to `moderation/models.py`:

```python
class ReportCategory(models.TextChoices):
    SPAM = "spam", "Spam"
    ABUSE = "abuse", "Abuse"
    ILLEGAL = "illegal", "Illegal"
    OTHER = "other", "Other"


class ReportStatus(models.TextChoices):
    OPEN = "open", "Open"
    ASSIGNED = "assigned", "Assigned"
    RESOLVED = "resolved", "Resolved"
    REJECTED = "rejected", "Rejected"


class Report(UUIDModel):
    target_actor = models.ForeignKey(
        "actors.Actor",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports_about",
    )
    target_page = models.ForeignKey(
        "comics.Page",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports_about",
    )
    target_comment = models.ForeignKey(
        "social.Comment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports_about",
    )
    reporter_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports_made",
    )
    reporter_actor = models.ForeignKey(
        "actors.Actor",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports_sent",
    )
    reason = models.TextField()
    category = models.CharField(
        max_length=16, choices=ReportCategory.choices, default=ReportCategory.OTHER
    )
    status = models.CharField(
        max_length=16, choices=ReportStatus.choices, default=ReportStatus.OPEN
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_reports",
    )
    resolution = models.TextField(blank=True, default="")
    forwarded_at = models.DateTimeField(null=True, blank=True)
    remote_activity_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        target_actor__isnull=False,
                        target_page__isnull=True,
                        target_comment__isnull=True,
                    )
                    | models.Q(
                        target_actor__isnull=True,
                        target_page__isnull=False,
                        target_comment__isnull=True,
                    )
                    | models.Q(
                        target_actor__isnull=True,
                        target_page__isnull=True,
                        target_comment__isnull=False,
                    )
                ),
                name="report_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        reporter_user__isnull=False, reporter_actor__isnull=True
                    )
                    | models.Q(
                        reporter_user__isnull=True, reporter_actor__isnull=False
                    )
                ),
                name="report_exactly_one_reporter",
            ),
            models.UniqueConstraint(
                fields=["remote_activity_id"],
                condition=~models.Q(remote_activity_id=""),
                name="unique_remote_report_activity",
            ),
        ]


class ModerationNote(UUIDModel):
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="moderation_notes",
    )
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="moderation_notes"
    )
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]


class ModerationAudit(UUIDModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderation_audit",
    )
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=255, blank=True, default="")
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
```

Run: `uv run python manage.py makemigrations moderation`
Expected: `moderation/migrations/0003_reports.py`.

- [ ] **Step 4: Implement the services**

Create `moderation/reports.py`:

```python
from uuid import UUID, uuid4

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from actors.models import Actor
from comics import permissions
from comics.models import Comic, ComicRole, Page
from moderation import blocks as block_services
from moderation.models import (
    ModerationAudit,
    ModerationNote,
    Report,
    ReportCategory,
    ReportStatus,
)
from social.models import Comment


class InvalidTargetError(Exception):
    pass


class InvalidReportError(Exception):
    pass


class NotPermittedError(Exception):
    pass


class NotForwardableError(Exception):
    pass


class ReportClosedError(Exception):
    pass


MODERATOR_ROLES = {
    ComicRole.Role.OWNER,
    ComicRole.Role.EDITOR,
    ComicRole.Role.MODERATOR,
}


def record_action(actor, action, *, target="", data=None):
    return ModerationAudit.objects.create(
        actor=actor, action=action, target=target, data=data or {}
    )


def target_comic(report):
    if report.target_page_id:
        return report.target_page.series.comic
    if report.target_comment_id:
        return report.target_comment.page.series.comic
    if report.target_actor_id:
        return Comic.objects.filter(actor=report.target_actor).first()
    return None


def can_view(user, report):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff:
        return True
    comic = target_comic(report)
    return comic is not None and permissions.can_moderate(user, comic)


def visible_reports(user):
    if user.is_staff:
        return Report.objects.all()
    comic_ids = list(
        ComicRole.objects.filter(
            user=user, role__in=MODERATOR_ROLES
        ).values_list("comic_id", flat=True)
    )
    return Report.objects.filter(
        Q(target_page__series__comic_id__in=comic_ids)
        | Q(target_comment__page__series__comic_id__in=comic_ids)
        | Q(target_actor__comic_id__in=comic_ids)
    )


def _validate_category(category):
    values = {choice.value for choice in ReportCategory}
    if category not in values:
        raise InvalidReportError
    return category


def _resolve_target(target_type, target_id):
    try:
        target_id = UUID(str(target_id))
    except (TypeError, ValueError) as exc:
        raise InvalidTargetError from exc
    if target_type == "actor":
        target = Actor.objects.filter(pk=target_id).first()
    elif target_type == "page":
        target = Page.objects.filter(pk=target_id).first()
    elif target_type == "comment":
        target = Comment.objects.filter(pk=target_id).first()
    else:
        raise InvalidTargetError
    if target is None:
        raise InvalidTargetError
    return target_type, target


def _open_query(user, target_type, target):
    query = Report.objects.filter(
        reporter_user=user,
        status__in=[ReportStatus.OPEN, ReportStatus.ASSIGNED],
    )
    return query.filter(**{f"target_{target_type}": target})


def report_target(
    user, *, target_type, target_id, reason, category=ReportCategory.OTHER, forward=False
):
    target_type, target = _resolve_target(target_type, target_id)
    if not isinstance(reason, str):
        raise InvalidReportError
    reason = reason.strip()
    if not reason:
        raise InvalidReportError
    category = _validate_category(category)
    report = _open_query(user, target_type, target).first()
    if report is None:
        report = Report.objects.create(
            reporter_user=user,
            reason=reason,
            category=category,
            **{f"target_{target_type}": target},
        )
        record_action(
            user,
            "report.created",
            target=f"report:{report.id}",
            data={"target_type": target_type, "target_id": str(target.id)},
        )
    else:
        report.reason = reason
        report.category = category
        report.save(update_fields=["reason", "category", "updated_at"])
    if forward:
        forward_report(report)
    return report


def forward_report(report):
    if report.reporter_user is None:
        raise NotForwardableError
    if report.target_actor_id:
        actor = report.target_actor
        object_url = actor.ap_id
    elif report.target_comment_id:
        actor = report.target_comment.actor
        object_url = report.target_comment.ap_id
    else:
        raise NotForwardableError
    if actor.is_local:
        raise NotForwardableError
    destination = actor.shared_inbox or actor.inbox
    if not destination:
        raise NotForwardableError
    reporter = block_services.personal_actor(report.reporter_user)
    if reporter is None:
        raise block_services.NoActorError
    from federation.activitypub import build_activity
    from federation.delivery import fan_out

    activity_id = f"{reporter.ap_id}#flags/{uuid4()}"
    payload = build_activity("Flag", reporter, object_url, activity_id=activity_id)
    payload["content"] = f"{report.category}: {report.reason}"
    fan_out(payload, [destination], reporter)
    report.forwarded_at = timezone.now()
    report.save(update_fields=["forwarded_at", "updated_at"])
    record_action(
        report.reporter_user,
        "report.forwarded",
        target=f"report:{report.id}",
        data={"object": object_url, "activity": activity_id},
    )
    return report


def assign_report(report, moderator, assigned_to=None):
    if not moderator.is_staff:
        raise NotPermittedError
    target = assigned_to or moderator
    if not target.is_staff:
        raise NotPermittedError
    if report.status not in (ReportStatus.OPEN, ReportStatus.ASSIGNED):
        raise ReportClosedError
    report.status = ReportStatus.ASSIGNED
    report.assigned_to = target
    report.save(update_fields=["status", "assigned_to", "updated_at"])
    record_action(
        moderator,
        "report.assigned",
        target=f"report:{report.id}",
        data={"assigned_to": str(target.id)},
    )
    return report


def resolve_report(report, moderator, status, resolution):
    if not can_view(moderator, report):
        raise NotPermittedError
    if status not in (ReportStatus.RESOLVED, ReportStatus.REJECTED):
        raise InvalidReportError
    if report.status in (ReportStatus.RESOLVED, ReportStatus.REJECTED):
        raise ReportClosedError
    if not isinstance(resolution, str):
        raise InvalidReportError
    resolution = resolution.strip()
    if not resolution:
        raise InvalidReportError
    report.status = status
    report.resolution = resolution
    report.save(update_fields=["status", "resolution", "updated_at"])
    record_action(
        moderator,
        f"report.{status}",
        target=f"report:{report.id}",
        data={"resolution": resolution},
    )
    return report


def comment_action(report, user, action):
    comment = report.target_comment
    if comment is None:
        raise InvalidTargetError
    comic = comment.page.series.comic
    if not permissions.can_moderate(user, comic):
        raise NotPermittedError
    from social import moderation as comment_moderation

    if action == "hide":
        comment_moderation.hide_comment(user, comment)
        audit_action = "comment.hidden"
    elif action == "approve":
        comment_moderation.approve_comment(user, comment)
        audit_action = "comment.approved"
    elif action == "ban_commenter":
        comment_moderation.ban_commenter(user, comic, comment.actor)
        audit_action = "commenter.banned"
    else:
        raise InvalidReportError
    record_action(user, audit_action, target=f"report:{report.id}")
    return comment


def add_note(user, actor, body):
    if not user.is_staff:
        raise NotPermittedError
    if not isinstance(body, str):
        raise InvalidReportError
    body = body.strip()
    if not body:
        raise InvalidReportError
    note = ModerationNote.objects.create(author=user, actor=actor, body=body)
    record_action(
        user,
        "note.created",
        target=f"actor:{actor.id}",
        data={"note_id": str(note.id)},
    )
    return note


def notes_for(user, actor):
    if not user.is_staff:
        raise NotPermittedError
    return ModerationNote.objects.filter(actor=actor).select_related("author")


def audit_for(user, *, target="", limit=100):
    if not user.is_staff:
        raise NotPermittedError
    query = ModerationAudit.objects.all()
    if target:
        query = query.filter(target=target)
    return query[: min(int(limit), 500)]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_moderation_reports.py -q`
Expected: `6 passed`

- [ ] **Step 6: Lint, migration check, and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
uv run python manage.py makemigrations --check --dry-run
git add moderation/models.py moderation/migrations/0003_reports.py moderation/reports.py tests/test_moderation_reports.py
git commit -m "feat: add reports, notes, and audit models"
```

---

### Task 2: Forwarding and inbound Flag

**Files:**
- Modify: `moderation/handlers.py`
- Modify: `moderation/reports.py`
- Modify: `tests/test_moderation_reports.py`

**Interfaces:**
- Consumes: Task 1 models/services, `federation.handlers.register`, `Activity`.
- Produces: `reports.record_inbound_flag(activity)`; registered `Flag` handler.

- [ ] **Step 1: Add failing forwarding and inbound tests**

Add `from federation.models import Activity`,
`from moderation import blocks as block_services`, and
`from actors.services import create_local_actor` to the top imports of
`tests/test_moderation_reports.py`, then append:

```python


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
    report = reports.report_target(user, target_type="actor", target_id=actor.id, reason="x")
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
    report = reports.report_target(
        user, target_type="comment", target_id=comment.id, reason="x", forward=True
    )
    payload, inboxes = sent[0]
    assert payload["object"] == comment.ap_id
    assert inboxes == ["https://remote.test/inbox"]


@pytest.mark.django_db
def test_inbound_flag_stores_and_dedupes():
    _, _, page, comment = _scene()
    remote = _remote(handle="reporter")
    payload = {
        "id": "https://remote.test/flags/1",
        "type": "Flag",
        "actor": remote.ap_id,
        "object": page.ap_id,
        "content": "spam",
    }
    data = reports.record_inbound_flag(_activity(remote, payload))
    assert data.reporter_actor == remote
    assert data.target_page == page
    assert data.remote_activity_id == payload["id"]
    again = reports.record_inbound_flag(_activity(remote, payload))
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_moderation_reports.py -q -k "forward or inbound_flag"`
Expected: `AttributeError: module 'moderation.reports' has no attribute 'fan_out'`/`record_inbound_flag` failures.

- [ ] **Step 3: Implement inbound Flag and the module-level delivery seam**

In `moderation/reports.py`, move the delivery imports to the top so tests can
monkeypatch them:

```python
from federation.activitypub import build_activity
from federation.delivery import fan_out
```

Remove the local imports inside `forward_report`.

Append:

```python
def record_inbound_flag(activity):
    raw = activity.payload.get("object")
    if isinstance(raw, dict):
        raw = raw.get("id") or raw.get("object")
    object_url = raw if isinstance(raw, str) else ""
    if not object_url:
        return None
    target_field = None
    target = None
    target = Actor.objects.filter(ap_id=object_url, domain="").first()
    if target is not None:
        target_field = "target_actor"
    else:
        target = Page.objects.filter(ap_id=object_url).first()
        if target is not None:
            target_field = "target_page"
    if target is None:
        target = Comment.objects.filter(ap_id=object_url).first()
        if target is not None:
            target_field = "target_comment"
    if target is None:
        return None
    report, created = Report.objects.get_or_create(
        remote_activity_id=activity.ap_id,
        defaults={
            "reporter_actor": activity.actor,
            "reason": str(activity.payload.get("content") or "remote flag"),
            "category": ReportCategory.OTHER,
            target_field: target,
        },
    )
    if created:
        record_action(
            None,
            "report.created",
            target=f"report:{report.id}",
            data={"remote_actor": activity.actor.ap_id, "activity": activity.ap_id},
        )
    return report
```

In `moderation/handlers.py`:

```python
@handlers.register("Flag")
def handle_flag(activity):
    return reports.record_inbound_flag(activity)
```

(import `from moderation import reports`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_moderation_reports.py -q`
Expected: `12 passed`

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add moderation/reports.py moderation/handlers.py tests/test_moderation_reports.py
git commit -m "feat: forward reports and ingest inbound flags"
```

---

### Task 3: Queue endpoints, inline actions, notes, audit

**Files:**
- Modify: `moderation/reports.py`
- Modify: `moderation/views.py`
- Modify: `moderation/urls.py`
- Modify: `tests/test_moderation_reports.py`

**Interfaces:**
- Consumes: Task 1/2 services.
- Produces: `reports.queue_reports(user, *, status=None, assigned=None, limit=50)`; views `reports_queue`, `report_detail`, `assign_report`, `resolve_report`, `forward_report`, `comment_action`, `actor_notes`, `audit_log`; corresponding URLs.

- [ ] **Step 1: Add failing endpoint tests**

Append to `tests/test_moderation_reports.py`:

```python
@pytest.mark.django_db
def test_report_endpoint_creates_and_dedupes(client):
    user = _user()
    actor = _remote()
    client.force_login(user)
    response = client.post(
        "/moderation/reports",
        data='{"target_type": "actor", "target_id": "%s", "reason": "x"}' % actor.id,
        content_type="application/json",
    )
    assert response.status_code == 201
    first_id = response.json()["id"]
    response = client.post(
        "/moderation/reports",
        data='{"target_type": "actor", "target_id": "%s", "reason": "y"}'
        % actor.id,
        content_type="application/json",
    )
    assert response.json()["id"] == first_id
    assert Report.objects.count() == 1


@pytest.mark.django_db
def test_report_endpoint_validation(client):
    user = _user()
    client.force_login(user)
    assert (
        client.post(
            "/moderation/reports",
            data='{"target_type": "actor", "target_id": "%s", "reason": "x"}'
            % uuid4(),
            content_type="application/json",
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/moderation/reports",
            data='{"target_type": "actor", "target_id": "bad", "reason": "x"}',
            content_type="application/json",
        ).status_code
        == 404
    )
    actor = _remote()
    response = client.post(
        "/moderation/reports",
        data='{"target_type": "actor", "target_id": "%s", "reason": " "}' % actor.id,
        content_type="application/json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_queue_scoping_and_filters(client):
    owner, comic, page, comment = _scene()
    editor = _user()
    from comics.models import ComicRole

    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.MODERATOR)
    staff = _user(staff=True)
    page_report = reports.report_target(
        owner, target_type="page", target_id=page.id, reason="x"
    )
    reports.report_target(
        owner, target_type="actor", target_id=_remote().id, reason="x"
    )

    client.force_login(editor)
    ids = [item["id"] for item in client.get("/moderation/reports").json()["reports"]]
    assert ids == [str(page_report.id)]

    client.force_login(staff)
    assert len(client.get("/moderation/reports").json()["reports"]) == 2
    assert client.get("/moderation/reports?status=bogus").status_code == 400


@pytest.mark.django_db
def test_report_detail_hidden_from_outsiders(client):
    owner, _, page, _ = _scene()
    report = reports.report_target(owner, target_type="page", target_id=page.id, reason="x")
    client.force_login(_user())
    assert client.get(f"/moderation/reports/{report.id}").status_code == 404


@pytest.mark.django_db
def test_assign_is_staff_only(client):
    owner, comic, page, _ = _scene()
    report = reports.report_target(owner, target_type="page", target_id=page.id, reason="x")
    client.force_login(owner)
    assert client.post(f"/moderation/reports/{report.id}/assign").status_code == 403
    staff = _user(staff=True)
    client.force_login(staff)
    response = client.post(f"/moderation/reports/{report.id}/assign")
    assert response.status_code == 200
    assert response.json()["status"] == "assigned"


@pytest.mark.django_db
def test_resolve_and_closed_reports(client):
    owner, comic, page, _ = _scene()
    report = reports.report_target(owner, target_type="page", target_id=page.id, reason="x")
    client.force_login(owner)
    response = client.post(
        f"/moderation/reports/{report.id}/resolve",
        data='{"status": "resolved", "resolution": "handled"}',
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    again = client.post(
        f"/moderation/reports/{report.id}/resolve",
        data='{"status": "resolved", "resolution": "again"}',
        content_type="application/json",
    )
    assert again.status_code == 409
    bad = client.post(
        f"/moderation/reports/{report.id}/resolve",
        data='{"status": "open", "resolution": "x"}',
        content_type="application/json",
    )
    assert bad.status_code == 409


@pytest.mark.django_db
def test_comment_actions_from_queue(client):
    owner, comic, page, comment = _scene()
    report = reports.report_target(
        owner, target_type="comment", target_id=comment.id, reason="x"
    )
    client.force_login(owner)
    response = client.post(
        f"/moderation/reports/{report.id}/comment-action",
        data='{"action": "hide"}',
        content_type="application/json",
    )
    assert response.status_code == 200
    comment.refresh_from_db()
    assert comment.status == "hidden"
    assert ModerationAudit.objects.filter(action="comment.hidden").exists()
    assert (
        client.post(
            f"/moderation/reports/{report.id}/comment-action",
            data='{"action": "nope"}',
            content_type="application/json",
        ).status_code
        == 400
    )


@pytest.mark.django_db
def test_notes_and_audit_endpoints_are_staff_only(client):
    actor = _remote()
    staff = _user(staff=True)
    client.force_login(_user())
    assert client.get(f"/moderation/actors/{actor.id}/notes").status_code == 403
    assert client.get("/moderation/audit").status_code == 403
    client.force_login(staff)
    response = client.post(
        f"/moderation/actors/{actor.id}/notes",
        data='{"body": "watching"}',
        content_type="application/json",
    )
    assert response.status_code == 201
    assert client.get(f"/moderation/actors/{actor.id}/notes").json()["notes"][0][
        "body"
    ] == "watching"
    assert client.get("/moderation/audit").json()["audit"][0]["action"] == "note.created"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_moderation_reports.py -q -k "endpoint or queue or detail or assign or resolve or comment_actions or notes_and_audit"`
Expected: `404 != 201/200` (routes missing).

- [ ] **Step 3: Add queue helpers**

Append to `moderation/reports.py`:

```python
def queue_reports(user, *, status=None, assigned=None, limit=50):
    query = visible_reports(user).select_related(
        "target_actor",
        "target_page__series__comic",
        "target_comment__actor",
        "target_comment__page__series__comic",
        "reporter_user",
        "reporter_actor",
        "assigned_to",
    )
    if status and status != "all":
        if status not in {choice.value for choice in ReportStatus}:
            raise InvalidReportError
        query = query.filter(status=status)
    if assigned == "me":
        query = query.filter(assigned_to=user)
    elif assigned == "unassigned":
        query = query.filter(assigned_to__isnull=True)
    elif assigned not in (None, "", "any"):
        raise InvalidReportError
    return query.order_by("-created_at")[: min(int(limit), 200)]
```

- [ ] **Step 4: Implement the views and URLs**

Append to `moderation/views.py` (add `from moderation import reports as report_services` and `from actors.models import Actor` already imported; add `from moderation.models import Report`):

```python
def _report_target_type(report):
    if report.target_actor_id:
        return "actor", report.target_actor_id
    if report.target_page_id:
        return "page", report.target_page_id
    return "comment", report.target_comment_id


def _serialize_report(report):
    target_type, target_id = _report_target_type(report)
    return {
        "id": str(report.id),
        "target_type": target_type,
        "target_id": str(target_id),
        "reporter": report.reporter_user.email
        if report.reporter_user
        else report.reporter_actor.ap_id,
        "reason": report.reason,
        "category": report.category,
        "status": report.status,
        "assigned_to": report.assigned_to.email if report.assigned_to else None,
        "forwarded_at": report.forwarded_at.isoformat()
        if report.forwarded_at
        else None,
        "resolution": report.resolution,
        "created_at": report.created_at.isoformat(),
    }


def _bool(value):
    if not isinstance(value, bool):
        raise report_services.InvalidReportError
    return value


def _report_or_404(request, report_id):
    return get_object_or_404(
        report_services.visible_reports(request.user), pk=report_id
    )


@login_required
@require_http_methods(["GET", "POST"])
def reports_queue(request):
    if request.method == "GET":
        try:
            items = report_services.queue_reports(
                request.user,
                status=request.GET.get("status"),
                assigned=request.GET.get("assigned"),
                limit=int(request.GET.get("limit", 50)),
            )
        except (ValueError, report_services.InvalidReportError):
            return _error("invalid_query", 400)
        return JsonResponse(
            {"reports": [_serialize_report(report) for report in items]}
        )
    try:
        data = _payload(request)
        if data.get("target_type") not in {"actor", "page", "comment"}:
            return _error("invalid_target", 400)
        report = report_services.report_target(
            request.user,
            target_type=data.get("target_type"),
            target_id=data.get("target_id"),
            reason=data.get("reason"),
            category=data.get("category", "other"),
            forward=_bool(data.get("forward", False)),
        )
    except report_services.InvalidTargetError:
        raise Http404
    except report_services.InvalidReportError:
        return _error("invalid_report", 400)
    except block_services.NoActorError:
        return _error("no_actor", 409)
    except report_services.NotForwardableError:
        return _error("not_forwardable", 400)
    return JsonResponse(_serialize_report(report), status=201)


@login_required
@require_http_methods(["GET"])
def report_detail(request, report_id):
    report = _report_or_404(request, report_id)
    data = _serialize_report(report)
    if request.user.is_staff and report.target_actor_id:
        data["notes"] = [
            {"id": str(note.id), "body": note.body, "author": note.author.email}
            for note in report_services.notes_for(request.user, report.target_actor)
        ]
    return JsonResponse(data)


@login_required
@require_http_methods(["POST"])
def assign_report(request, report_id):
    report = _report_or_404(request, report_id)
    try:
        data = _payload(request)
        assigned = None
        if data.get("assigned_to"):
            assigned = get_user_model().objects.filter(
                pk=data["assigned_to"], is_staff=True
            ).first()
            if assigned is None:
                return _error("invalid_user", 400)
        report = report_services.assign_report(report, request.user, assigned)
    except report_services.NotPermittedError:
        return _error("not_permitted", 403)
    except report_services.ReportClosedError:
        return _error("closed", 409)
    return JsonResponse(_serialize_report(report))


@login_required
@require_http_methods(["POST"])
def resolve_report(request, report_id):
    report = _report_or_404(request, report_id)
    data = _payload(request)
    try:
        report = report_services.resolve_report(
            report, request.user, data.get("status"), data.get("resolution")
        )
    except report_services.NotPermittedError:
        return _error("not_permitted", 403)
    except report_services.InvalidReportError:
        return _error("invalid_report", 400)
    except report_services.ReportClosedError:
        return _error("closed", 409)
    return JsonResponse(_serialize_report(report))


@login_required
@require_http_methods(["POST"])
def forward_report(request, report_id):
    report = _report_or_404(request, report_id)
    if not report_services.can_view(request.user, report):
        return _error("not_permitted", 403)
    try:
        report = report_services.forward_report(report)
    except block_services.NoActorError:
        return _error("no_actor", 409)
    except report_services.NotForwardableError:
        return _error("not_forwardable", 400)
    return JsonResponse(_serialize_report(report))


@login_required
@require_http_methods(["POST"])
def comment_action(request, report_id):
    report = _report_or_404(request, report_id)
    data = _payload(request)
    try:
        report_services.comment_action(report, request.user, data.get("action"))
    except report_services.InvalidTargetError:
        return _error("invalid_target", 400)
    except report_services.NotPermittedError:
        return _error("not_permitted", 403)
    except report_services.InvalidReportError:
        return _error("invalid_action", 400)
    return JsonResponse(_serialize_report(report))


@login_required
@require_http_methods(["GET", "POST"])
def actor_notes(request, actor_id):
    actor = get_object_or_404(Actor, pk=actor_id)
    if request.method == "GET":
        try:
            notes = report_services.notes_for(request.user, actor)
        except report_services.NotPermittedError:
            return _error("not_permitted", 403)
        return JsonResponse(
            {
                "notes": [
                    {"id": str(note.id), "body": note.body, "author": note.author.email}
                    for note in notes
                ]
            }
        )
    try:
        data = _payload(request)
        note = report_services.add_note(request.user, actor, data.get("body"))
    except report_services.NotPermittedError:
        return _error("not_permitted", 403)
    except report_services.InvalidReportError:
        return _error("invalid_body", 400)
    return JsonResponse(
        {"id": str(note.id), "body": note.body, "author": note.author.email}, status=201
    )


@login_required
@require_http_methods(["GET"])
def audit_log(request):
    try:
        entries = report_services.audit_for(
            request.user,
            target=request.GET.get("target", ""),
            limit=int(request.GET.get("limit", 100)),
        )
    except (ValueError, report_services.NotPermittedError):
        return _error("not_permitted", 403)
    return JsonResponse(
        {
            "audit": [
                {
                    "id": str(entry.id),
                    "actor": entry.actor.email if entry.actor else None,
                    "action": entry.action,
                    "target": entry.target,
                    "data": entry.data,
                    "created_at": entry.created_at.isoformat(),
                }
                for entry in entries
            ]
        }
    )
```

Add `from django.contrib.auth import get_user_model` to the imports.

URLs:

```python
    path("moderation/reports", views.reports_queue, name="moderation-reports"),
    path(
        "moderation/reports/<uuid:report_id>",
        views.report_detail,
        name="moderation-report-detail",
    ),
    path(
        "moderation/reports/<uuid:report_id>/assign",
        views.assign_report,
        name="moderation-report-assign",
    ),
    path(
        "moderation/reports/<uuid:report_id>/resolve",
        views.resolve_report,
        name="moderation-report-resolve",
    ),
    path(
        "moderation/reports/<uuid:report_id>/forward",
        views.forward_report,
        name="moderation-report-forward",
    ),
    path(
        "moderation/reports/<uuid:report_id>/comment-action",
        views.comment_action,
        name="moderation-report-comment-action",
    ),
    path(
        "moderation/actors/<uuid:actor_id>/notes",
        views.actor_notes,
        name="moderation-actor-notes",
    ),
    path("moderation/audit", views.audit_log, name="moderation-audit"),
```

- [ ] **Step 5: Run the full report file**

Run: `uv run pytest tests/test_moderation_reports.py -q`
Expected: `20 passed`

- [ ] **Step 6: Run the full suite, lint, and migration check**

Run: `uv run pytest -q`
Expected: previous total + new tests, no failures.

Run: `uv run ruff check .` and `uv run ruff format --check .`
Expected: clean.

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`

- [ ] **Step 7: Commit**

```bash
git add moderation/reports.py moderation/views.py moderation/urls.py tests/test_moderation_reports.py
git commit -m "feat: expose the reports queue and moderation audit"
```
