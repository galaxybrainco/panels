from actors.models import Actor
from comics.federation import federation_plan
from comics.models import Page, PageStatus
from federation import handlers
from social import services
from social.models import Boost, Follow, FollowStatus, Like


def _federatable_page(object_id):
    page = Page.objects.filter(ap_id=object_id, status=PageStatus.PUBLISHED).first()
    if page is None or not federation_plan(page).emit:
        return None
    return page


@handlers.register("Follow")
def handle_follow(activity):
    target = Actor.objects.filter(
        ap_id=activity.payload.get("object"), domain=""
    ).first()
    if target is None:
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
                follower=activity.actor, target__ap_id=obj.get("object")
            ).delete()
        elif obj_type == "Like":
            Like.objects.filter(
                actor=activity.actor, object_id=obj.get("object")
            ).delete()
        elif obj_type == "Announce":
            Boost.objects.filter(
                actor=activity.actor, object_id=obj.get("object")
            ).delete()
    elif isinstance(obj, str):
        Follow.objects.filter(follower=activity.actor, activity_id=obj).delete()
        Like.objects.filter(actor=activity.actor, activity_id=obj).delete()
        Boost.objects.filter(actor=activity.actor, activity_id=obj).delete()


@handlers.register("Like")
def handle_like(activity):
    object_id = activity.payload.get("object")
    page = _federatable_page(object_id)
    if page is None:
        return
    Like.objects.get_or_create(
        actor=activity.actor,
        object_id=object_id,
        defaults={"page": page, "activity_id": activity.ap_id},
    )


@handlers.register("Announce")
def handle_announce(activity):
    object_id = activity.payload.get("object")
    page = _federatable_page(object_id)
    if page is None:
        return
    Boost.objects.get_or_create(
        actor=activity.actor,
        object_id=object_id,
        defaults={"page": page, "activity_id": activity.ap_id},
    )


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
