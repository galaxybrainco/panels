from django.db import models

from actors.models import Actor

ACTIVITYSTREAMS_CONTEXT = "https://www.w3.org/ns/activitystreams"
SECURITY_CONTEXT = "https://w3id.org/security/v1"
PUBLIC = "https://www.w3.org/ns/activitystreams#Public"


class Audience(models.TextChoices):
    PUBLIC = "public", "Public"
    UNLISTED = "unlisted", "Unlisted"
    FOLLOWERS_ONLY = "followers_only", "Followers only"
    MEMBERS = "members", "Members"
    TIER = "tier", "Tier"


def activity_context():
    return [ACTIVITYSTREAMS_CONTEXT, SECURITY_CONTEXT]


def build_activity(
    activity_type, actor: Actor, obj, *, activity_id=None, to=None, cc=None
):
    activity = {
        "@context": activity_context(),
        "type": activity_type,
        "actor": actor.ap_id,
        "object": obj,
    }
    if activity_id is not None:
        activity["id"] = activity_id
    if to is not None:
        activity["to"] = to
    if cc is not None:
        activity["cc"] = cc
    return activity


def addressing_for(audience, followers_url):
    if audience == Audience.PUBLIC:
        return [PUBLIC], [followers_url]
    if audience == Audience.UNLISTED:
        return [followers_url], [PUBLIC]
    if audience == Audience.FOLLOWERS_ONLY:
        return [followers_url], []
    return [], []


def is_public(activity) -> bool:
    recipients = list(activity.get("to") or []) + list(activity.get("cc") or [])
    return PUBLIC in recipients


def ordered_collection(
    collection_id, *, total_items=0, items=None, first=None, last=None
):
    document = {
        "@context": ACTIVITYSTREAMS_CONTEXT,
        "id": collection_id,
        "type": "OrderedCollection",
        "totalItems": total_items,
    }
    if items is not None:
        document["orderedItems"] = items
    if first is not None:
        document["first"] = first
    if last is not None:
        document["last"] = last
    return document


def ordered_collection_page(
    page_id, part_of, ordered_items, *, next_url=None, prev_url=None
):
    document = {
        "@context": ACTIVITYSTREAMS_CONTEXT,
        "id": page_id,
        "type": "OrderedCollectionPage",
        "partOf": part_of,
        "orderedItems": ordered_items,
    }
    if next_url is not None:
        document["next"] = next_url
    if prev_url is not None:
        document["prev"] = prev_url
    return document
