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


def _paginate(collection_url, items, page, page_size):
    total = len(items)
    if page is None:
        return ordered_collection(
            collection_url, total_items=total, first=f"{collection_url}?page=1"
        )
    start = (page - 1) * page_size
    document = ordered_collection_page(
        f"{collection_url}?page={page}",
        collection_url,
        items[start : start + page_size],
    )
    if start + page_size < total:
        document["next"] = f"{collection_url}?page={page + 1}"
    if page > 1:
        document["prev"] = f"{collection_url}?page={page - 1}"
    return document


def actor_outbox(actor, *, page=None, page_size=PAGE_SIZE):
    items = [activity.payload for activity in _public_outbound(actor)]
    return _paginate(actor.outbox, items, page, page_size)


def _accepted_followers(actor):
    from social.models import Follow, FollowStatus

    return [
        follow.follower.ap_id
        for follow in Follow.objects.filter(target=actor, status=FollowStatus.ACCEPTED)
        .select_related("follower")
        .order_by("created_at")
    ]


def _accepted_following(actor):
    from social.models import Follow, FollowStatus

    return [
        follow.target.ap_id
        for follow in Follow.objects.filter(
            follower=actor, status=FollowStatus.ACCEPTED
        )
        .select_related("target")
        .order_by("created_at")
    ]


def actor_followers(actor, *, page=None, page_size=PAGE_SIZE):
    return _paginate(actor.followers, _accepted_followers(actor), page, page_size)


def actor_following(actor, *, page=None, page_size=PAGE_SIZE):
    return _paginate(actor.following, _accepted_following(actor), page, page_size)


def empty_collection(url):
    return ordered_collection(url, total_items=0)
