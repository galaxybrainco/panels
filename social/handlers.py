from actors.models import Actor
from federation import handlers
from social import services
from social.comments import federatable_page, reply_target, store_inbound_reply
from social.models import Boost, Comment, CommentStatus, Follow, FollowStatus, Like


def _resolve_reaction_target(object_id):
    comment = Comment.objects.filter(
        ap_id=object_id, status=CommentStatus.VISIBLE
    ).first()
    if comment is not None:
        page = federatable_page(comment.page.ap_id)
        if page is None:
            return None, None
        return page, comment
    page = federatable_page(object_id)
    if page is None:
        return None, None
    return page, None


@handlers.register("Create")
def handle_create(activity):
    from social.tasks import crawl_comment_thread

    comment = store_inbound_reply(
        activity.payload.get("object"), activity.actor, activity_id=activity.ap_id
    )
    if comment is not None:
        crawl_comment_thread.enqueue(str(comment.id))


@handlers.register("Follow")
def handle_follow(activity):
    target = Actor.objects.filter(
        ap_id=activity.payload.get("object"), domain=""
    ).first()
    if target is None:
        return
    from moderation import accounts

    if not accounts.publicly_visible(target):
        return
    follow_obj, created = Follow.objects.get_or_create(
        follower=activity.actor,
        target=target,
        defaults={"status": FollowStatus.PENDING, "activity_id": activity.ap_id},
    )
    if not created and not follow_obj.activity_id:
        follow_obj.activity_id = activity.ap_id
        follow_obj.save(update_fields=["activity_id", "updated_at"])
    if created and not target.manually_approves_followers:
        services.accept_follow(follow_obj)


@handlers.register("Undo")
def handle_undo(activity):
    obj = activity.payload.get("object")
    if isinstance(obj, dict):
        obj_type = obj.get("type")
        if obj_type == "Follow":
            Follow.objects.filter(
                follower=activity.actor, target__ap_id=reply_target(obj.get("object"))
            ).delete()
        elif obj_type == "Like":
            Like.objects.filter(
                actor=activity.actor, object_id=reply_target(obj.get("object"))
            ).delete()
        elif obj_type == "Announce":
            Boost.objects.filter(
                actor=activity.actor, object_id=reply_target(obj.get("object"))
            ).delete()
        elif obj_type == "Block":
            from moderation import blocks as block_services

            block_services.handle_undo_block(activity, obj)
    elif isinstance(obj, str) and obj:
        Follow.objects.filter(follower=activity.actor, activity_id=obj).delete()
        Like.objects.filter(actor=activity.actor, activity_id=obj).delete()
        Boost.objects.filter(actor=activity.actor, activity_id=obj).delete()
        from moderation.models import Block

        Block.objects.filter(blocker=activity.actor, activity_id=obj).delete()


@handlers.register("Like")
def handle_like(activity):
    object_id = reply_target(activity.payload.get("object"))
    page, comment = _resolve_reaction_target(object_id)
    if page is None:
        return
    like_obj, created = Like.objects.get_or_create(
        actor=activity.actor,
        object_id=object_id,
        defaults={"page": page, "comment": comment, "activity_id": activity.ap_id},
    )
    if not created and not like_obj.activity_id:
        like_obj.activity_id = activity.ap_id
        like_obj.save(update_fields=["activity_id", "updated_at"])
    if created:
        from social.notifications import notify_like

        notify_like(like_obj)


@handlers.register("Announce")
def handle_announce(activity):
    object_id = reply_target(activity.payload.get("object"))
    page, comment = _resolve_reaction_target(object_id)
    if page is None:
        return
    boost_obj, created = Boost.objects.get_or_create(
        actor=activity.actor,
        object_id=object_id,
        defaults={"page": page, "comment": comment, "activity_id": activity.ap_id},
    )
    if not created and not boost_obj.activity_id:
        boost_obj.activity_id = activity.ap_id
        boost_obj.save(update_fields=["activity_id", "updated_at"])
    if created:
        from social.notifications import notify_boost

        notify_boost(boost_obj)


@handlers.register("Accept")
def handle_accept(activity):
    obj = activity.payload.get("object")
    target = activity.actor
    if isinstance(obj, dict) and obj.get("type") == "Follow":
        if obj.get("object") != target.ap_id:
            return
        follows = Follow.objects.filter(
            target=target, follower__domain="", status=FollowStatus.PENDING
        )
        if obj.get("actor"):
            follows = follows.filter(follower__ap_id=obj["actor"])
    elif isinstance(obj, str):
        follows = Follow.objects.filter(
            activity_id=obj, target=target, status=FollowStatus.PENDING
        )
    else:
        return
    for follow_obj in follows:
        follow_obj.status = FollowStatus.ACCEPTED
        follow_obj.save(update_fields=["status", "updated_at"])
