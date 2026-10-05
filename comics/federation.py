from dataclasses import dataclass, field
from uuid import uuid4

from django.conf import settings
from django.utils.html import escape

from comics.models import FederationMode
from federation.activitypub import (
    Audience,
    activity_context,
    addressing_for,
    build_activity,
)
from federation.models import Activity, ActivityDirection, ActivityStatus
from media.derivative_urls import DerivativeKind, derivative_url


@dataclass(frozen=True)
class FederationPlan:
    emit: bool
    to: list = field(default_factory=list)
    cc: list = field(default_factory=list)


def _page_url(page) -> str:
    return f"{settings.INSTANCE_URL}/pages/{page.id}"


def _note_id(page) -> str:
    return page.ap_id or _page_url(page)


def federation_plan(page) -> FederationPlan:
    if page.federation == FederationMode.LOCAL_ONLY:
        return FederationPlan(emit=False)
    if page.audience in (Audience.MEMBERS, Audience.TIER):
        return FederationPlan(emit=False)
    to, cc = addressing_for(page.audience, page.series.comic.actor.followers)
    return FederationPlan(emit=True, to=list(to), cc=list(cc))


def _note_content(page, url) -> str:
    parts = []
    if page.title:
        parts.append(f"<p>{escape(page.title)}</p>")
    lines = page.author_commentary.strip().splitlines()
    if lines:
        parts.append(f"<p>{escape(lines[0])}</p>")
    parts.append(f'<p><a href="{escape(url)}">Read on Panels</a></p>')
    return "".join(parts)


def _attachment(media) -> dict:
    attachment = {
        "type": "Document",
        "mediaType": media.content_type,
        "url": derivative_url(media, DerivativeKind.FEDERATION),
        "name": media.alt_text,
    }
    if media.width and media.height:
        attachment["width"] = media.width
        attachment["height"] = media.height
    return attachment


def page_to_note(page, plan=None) -> dict:
    plan = plan or federation_plan(page)
    note_id = _note_id(page)
    note = {
        "@context": activity_context(),
        "id": note_id,
        "type": "Note",
        "attributedTo": page.series.comic.actor.ap_id,
        "url": note_id,
        "sensitive": page.sensitive,
        "content": _note_content(page, note_id),
        "to": plan.to,
        "cc": plan.cc,
        "attachment": [_attachment(media) for media in page.media.all()],
    }
    if page.published_at is not None:
        note["published"] = page.published_at.isoformat()
    if page.content_warning:
        note["summary"] = page.content_warning
    return note


def emit_page_activity(page, activity_type, plan=None):
    plan = plan or federation_plan(page)
    if not plan.emit:
        return None
    note_id = _note_id(page)
    obj = note_id if activity_type == "Delete" else page_to_note(page, plan=plan)
    activity_id = f"{note_id}/activities/{uuid4()}"
    payload = build_activity(
        activity_type,
        page.series.comic.actor,
        obj,
        activity_id=activity_id,
        to=plan.to,
        cc=plan.cc,
    )
    return Activity.objects.create(
        ap_id=activity_id,
        type=activity_type,
        actor=page.series.comic.actor,
        direction=ActivityDirection.OUTBOUND,
        status=ActivityStatus.PROCESSED,
        payload=payload,
    )
