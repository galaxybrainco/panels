from uuid import UUID, uuid4

from django.db.models import Q
from django.utils import timezone

from actors.models import Actor
from comics import permissions
from comics.models import Comic, ComicRole, Page
from federation.activitypub import build_activity
from federation.delivery import fan_out
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
        ComicRole.objects.filter(user=user, role__in=MODERATOR_ROLES).values_list(
            "comic_id", flat=True
        )
    )
    return Report.objects.filter(
        Q(target_page__series__comic_id__in=comic_ids)
        | Q(target_comment__page__series__comic_id__in=comic_ids)
        | Q(target_actor__comic__id__in=comic_ids)
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
    user,
    *,
    target_type,
    target_id,
    reason,
    category=ReportCategory.OTHER,
    forward=False,
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


def record_inbound_flag(activity):
    raw = activity.payload.get("object")
    if isinstance(raw, dict):
        raw = raw.get("id") or raw.get("object")
    object_url = raw if isinstance(raw, str) else ""
    if not object_url:
        return None
    target_field = None
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
