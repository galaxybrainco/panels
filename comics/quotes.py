from uuid import uuid4

from django.conf import settings

from comics.federation import federation_plan
from comics.models import Page, PageStatus, QuotePolicy, QuoteRequest, QuoteStatus
from federation.activitypub import build_activity
from federation.delivery import fan_out
from moderation import accounts
from moderation.reports import NotPermittedError, record_action


class InvalidPolicyError(Exception):
    pass


class QuoteClosedError(Exception):
    pass


def _inbox(actor):
    return actor.shared_inbox or actor.inbox


def _quoted_url(note):
    for key in ("quote", "quoteUrl", "_misskey_quote"):
        value = note.get(key)
        if isinstance(value, dict):
            value = value.get("id") or value.get("href")
        if isinstance(value, str) and value:
            return value
    for tag in note.get("tag") or []:
        if not isinstance(tag, dict) or tag.get("type") != "Link":
            continue
        href = tag.get("href")
        media = str(tag.get("mediaType") or "")
        name = str(tag.get("name") or "").upper()
        if href and ("ld+json" in media or name.startswith("RE:")):
            return href
    return ""


def authorization_object(request):
    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": request.authorization_id,
        "type": "QuoteAuthorization",
        "attributedTo": request.page.series.comic.actor.ap_id,
        "interactingObject": request.quote_id,
        "interactionTarget": request.page.ap_id,
    }


def _decide(request, approved):
    page = request.page
    actor = page.series.comic.actor
    if approved:
        request.status = QuoteStatus.APPROVED
        if not request.authorization_id:
            request.authorization_id = (
                f"{settings.INSTANCE_URL}/quote-authorizations/{uuid4()}"
            )
        activity_type = "Accept"
        obj = authorization_object(request)
    else:
        request.status = QuoteStatus.REJECTED
        activity_type = "Reject"
        obj = request.quote_id
    request.save(update_fields=["status", "authorization_id", "updated_at"])
    payload = build_activity(
        activity_type,
        actor,
        obj,
        activity_id=f"{actor.ap_id}#quote-decisions/{uuid4()}",
    )
    fan_out(payload, [_inbox(request.actor)], actor)
    return request


def process_inbound_quote(activity):
    note = activity.payload.get("object")
    if not isinstance(note, dict) or note.get("type") != "Note":
        return None
    quote_id = note.get("id")
    target = _quoted_url(note)
    if not quote_id or not target:
        return None
    page = (
        Page.objects.filter(ap_id=target, status=PageStatus.PUBLISHED)
        .select_related("series__comic__actor")
        .first()
    )
    if page is None or not federation_plan(page).emit:
        return None
    if not accounts.publicly_visible(page.series.comic.actor):
        return None
    request, created = QuoteRequest.objects.get_or_create(
        quote_id=quote_id,
        defaults={
            "page": page,
            "actor": activity.actor,
            "activity_id": activity.ap_id,
        },
    )
    if not created:
        return request
    if page.quote_policy == QuotePolicy.ALLOW:
        _decide(request, True)
    elif page.quote_policy == QuotePolicy.DENY:
        _decide(request, False)
    return request


def _require_owner(user, comic):
    from comics import permissions

    if not permissions.is_owner(user, comic):
        raise NotPermittedError


def set_quote_policy(page, user, policy):
    _require_owner(user, page.series.comic)
    values = {choice.value for choice in QuotePolicy}
    if policy not in values:
        raise InvalidPolicyError
    page.quote_policy = policy
    page.save(update_fields=["quote_policy", "updated_at"])
    record_action(
        user,
        "page.quote_policy_changed",
        target=f"page:{page.id}",
        data={"policy": policy},
    )
    return page


def quote_requests(comic, user, status=None):
    _require_owner(user, comic)
    query = QuoteRequest.objects.filter(page__series__comic=comic).select_related(
        "page__series", "actor"
    )
    if status:
        query = query.filter(status=status)
    return query.order_by("-created_at")


def decide_quote(request, user, approve):
    _require_owner(user, request.page.series.comic)
    if request.status != QuoteStatus.PENDING:
        raise QuoteClosedError
    result = _decide(request, approve)
    record_action(
        user,
        "quote.approved" if approve else "quote.rejected",
        target=f"quote:{request.id}",
    )
    return result
