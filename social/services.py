from social.models import Follow, FollowStatus


def follower_inboxes(actor):
    follows = (
        Follow.objects.filter(
            target=actor,
            status=FollowStatus.ACCEPTED,
            follower__domain__gt="",
        )
        .select_related("follower")
        .order_by("created_at")
    )
    inboxes = []
    for follow in follows:
        url = follow.follower.shared_inbox or follow.follower.inbox
        if url:
            inboxes.append(url)
    return inboxes
