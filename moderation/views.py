import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from actors.models import Actor
from comics.models import Page
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
