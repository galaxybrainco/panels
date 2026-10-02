from federation.activitypub import (
    PUBLIC,
    ordered_collection,
    ordered_collection_page,
)
from federation.models import Activity, ActivityDirection, ActivityStatus

PAGE_SIZE = 20


def _public_outbound(actor):
    activities = Activity.objects.filter(
        actor=actor,
        direction=ActivityDirection.OUTBOUND,
        status=ActivityStatus.PROCESSED,
    ).order_by("-created_at")
    return [activity for activity in activities if _is_public(activity)]


def _is_public(activity):
    payload = activity.payload
    recipients = list(payload.get("to") or []) + list(payload.get("cc") or [])
    return PUBLIC in recipients


def actor_outbox(actor, *, page=None, page_size=PAGE_SIZE):
    activities = _public_outbound(actor)
    total = len(activities)
    collection_url = actor.outbox
    if page is None:
        return ordered_collection(
            collection_url, total_items=total, first=f"{collection_url}?page=1"
        )
    start = (page - 1) * page_size
    items = [activity.payload for activity in activities[start : start + page_size]]
    document = ordered_collection_page(
        f"{collection_url}?page={page}", collection_url, items
    )
    if start + page_size < total:
        document["next"] = f"{collection_url}?page={page + 1}"
    if page > 1:
        document["prev"] = f"{collection_url}?page={page - 1}"
    return document


def empty_collection(url):
    return ordered_collection(url, total_items=0)
