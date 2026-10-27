import json

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from actors.models import Actor
from comics.models import Page
from moderation import blocks as block_services
from moderation import reports as report_services
from moderation import services
from moderation.models import KeywordFilter
from social.models import Follow


def _payload(request):
    if not request.body.strip():
        return {}
    content_type = request.content_type or ""
    if not content_type.startswith("application/json"):
        if content_type.startswith("multipart/form-data") and not request.POST:
            return {}
        raise services.InvalidPayloadError
    try:
        data = json.loads(request.body)
    except ValueError as exc:
        raise services.InvalidPayloadError from exc
    if not isinstance(data, dict):
        raise services.InvalidPayloadError
    return data


def _expires_at(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise services.InvalidExpiryError
    parsed = parse_datetime(value)
    if parsed is None:
        raise services.InvalidExpiryError
    return parsed


def _error(code, status):
    return JsonResponse({"error": code}, status=status)


def _serialize_filter(keyword):
    return {
        "id": str(keyword.id),
        "phrase": keyword.phrase,
        "expires_at": keyword.expires_at.isoformat()
        if keyword.expires_at is not None
        else None,
    }


@login_required
@require_http_methods(["POST", "DELETE"])
def mutes(request, actor_id):
    actor = get_object_or_404(Actor, pk=actor_id)
    if request.method == "DELETE":
        services.unmute_actor(request.user, actor)
        return JsonResponse({"muted": False})
    try:
        data = _payload(request)
        hide = data.get("hide_notifications", False)
        if not isinstance(hide, bool):
            raise services.InvalidPayloadError
        mute = services.mute_actor(
            request.user,
            actor,
            hide_notifications=hide,
            expires_at=_expires_at(data.get("expires_at")),
        )
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
    except services.InvalidExpiryError:
        return _error("invalid_expiry", 400)
    except services.SelfMuteError:
        return _error("invalid_target", 400)
    return JsonResponse(
        {
            "muted": True,
            "hide_notifications": mute.hide_notifications,
            "expires_at": mute.expires_at.isoformat()
            if mute.expires_at is not None
            else None,
        }
    )


@login_required
@require_http_methods(["POST", "DELETE"])
def thread_mutes(request, page_id):
    page = get_object_or_404(Page, pk=page_id)
    if request.method == "DELETE":
        services.unmute_thread(request.user, page)
        return JsonResponse({"muted": False})
    services.mute_thread(request.user, page)
    return JsonResponse({"muted": True})


@login_required
@require_http_methods(["GET", "POST"])
def filters(request):
    if request.method == "GET":
        return JsonResponse(
            {
                "filters": [
                    _serialize_filter(keyword)
                    for keyword in services.active_filters(request.user)
                ]
            }
        )
    try:
        data = _payload(request)
        keyword = services.add_filter(
            request.user,
            data.get("phrase"),
            expires_at=_expires_at(data.get("expires_at")),
        )
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
    except services.InvalidExpiryError:
        return _error("invalid_expiry", 400)
    except services.InvalidPhraseError:
        return _error("invalid_phrase", 400)
    return JsonResponse(_serialize_filter(keyword))


@login_required
@require_http_methods(["DELETE"])
def filter_detail(request, filter_id):
    keyword = KeywordFilter.objects.filter(pk=filter_id, user=request.user).first()
    if keyword is None:
        raise Http404
    services.remove_filter(request.user, keyword.pk)
    return JsonResponse({"removed": True})


@login_required
@require_http_methods(["POST", "DELETE"])
def domain_blocks(request, domain):
    if request.method == "DELETE":
        services.unblock_domain(request.user, domain)
        return JsonResponse({"blocked": False})
    try:
        services.block_domain(request.user, domain)
    except services.InvalidDomainError:
        return _error("invalid_domain", 400)
    return JsonResponse({"blocked": True})


@login_required
@require_http_methods(["DELETE"])
def remove_follower(request, actor_id, follow_id):
    actor = get_object_or_404(Actor, pk=actor_id)
    follow = get_object_or_404(Follow, pk=follow_id)
    try:
        services.remove_follower(request.user, actor, follow)
    except services.NotActorOwnerError:
        return _error("not_owner", 403)
    except services.FollowNotFoundError as exc:
        raise Http404 from exc
    return JsonResponse({"removed": True})


@login_required
@require_http_methods(["POST", "DELETE"])
def blocks(request, actor_id):
    actor = get_object_or_404(Actor, pk=actor_id)
    if request.method == "DELETE":
        try:
            block_services.unblock_actor(request.user, actor)
        except block_services.NoActorError:
            return _error("no_actor", 409)
        return JsonResponse({"blocked": False})
    try:
        block_services.block_actor(request.user, actor)
    except block_services.NoActorError:
        return _error("no_actor", 409)
    except block_services.SelfBlockError:
        return _error("invalid_target", 400)
    return JsonResponse({"blocked": True})


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
        except ValueError, report_services.InvalidReportError:
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
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
    except report_services.InvalidTargetError as exc:
        raise Http404 from exc
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
            try:
                assigned = (
                    get_user_model()
                    .objects.filter(pk=data["assigned_to"], is_staff=True)
                    .first()
                )
            except ValidationError, ValueError, TypeError:
                return _error("invalid_user", 400)
            if assigned is None:
                return _error("invalid_user", 400)
        report = report_services.assign_report(report, request.user, assigned)
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
    except report_services.NotPermittedError:
        return _error("not_permitted", 403)
    except report_services.ReportClosedError:
        return _error("closed", 409)
    return JsonResponse(_serialize_report(report))


@login_required
@require_http_methods(["POST"])
def resolve_report(request, report_id):
    report = _report_or_404(request, report_id)
    try:
        data = _payload(request)
        report = report_services.resolve_report(
            report, request.user, data.get("status"), data.get("resolution")
        )
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
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
    try:
        report = report_services.forward_report(report, moderator=request.user)
    except report_services.ReportClosedError:
        return _error("closed", 409)
    except block_services.NoActorError:
        return _error("no_actor", 409)
    except report_services.NotForwardableError:
        return _error("not_forwardable", 400)
    return JsonResponse(_serialize_report(report))


@login_required
@require_http_methods(["POST"])
def comment_action(request, report_id):
    report = _report_or_404(request, report_id)
    try:
        data = _payload(request)
        report_services.comment_action(report, request.user, data.get("action"))
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
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
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
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
    except ValueError, report_services.NotPermittedError:
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
