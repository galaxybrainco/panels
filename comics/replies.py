from comics.models import ComicRole, ReplyPolicy
from federation.activitypub import PUBLIC
from moderation.reports import NotPermittedError, record_action


def _audiences(page):
    comic = page.series.comic
    if page.reply_policy == ReplyPolicy.NOBODY:
        return []
    if page.reply_policy == ReplyPolicy.FOLLOWERS:
        return [comic.actor.followers]
    return [PUBLIC]


def interaction_policy(page):
    audiences = _audiences(page)
    if page.series.comic.require_reply_approval:
        automatic = []
        manual = audiences
    else:
        automatic = audiences
        manual = []
    return {
        "canReply": {
            "automaticApproval": automatic,
            "manualApproval": manual,
        }
    }


def is_comic_owner(actor, comic):
    if actor is None or actor.user_id is None:
        return False
    return ComicRole.objects.filter(
        comic=comic, user_id=actor.user_id, role=ComicRole.Role.OWNER
    ).exists()


def can_reply(page, actor):
    if actor is None:
        return False
    comic = page.series.comic
    if is_comic_owner(actor, comic):
        return True
    if page.reply_policy == ReplyPolicy.NOBODY:
        return False
    if page.reply_policy == ReplyPolicy.FOLLOWERS:
        from social.models import Follow, FollowStatus

        return Follow.objects.filter(
            follower=actor, target=comic.actor, status=FollowStatus.ACCEPTED
        ).exists()
    return True


class InvalidPolicyError(Exception):
    pass


def set_reply_policy(page, user, policy):
    from comics import permissions

    if not permissions.is_owner(user, page.series.comic):
        raise NotPermittedError
    values = {choice.value for choice in ReplyPolicy}
    if policy not in values:
        raise InvalidPolicyError
    page.reply_policy = policy
    page.save(update_fields=["reply_policy", "updated_at"])
    record_action(
        user,
        "page.reply_policy_changed",
        target=f"page:{page.id}",
        data={"policy": policy},
    )
    return page
