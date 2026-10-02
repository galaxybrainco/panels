from comics.models import ComicRole


def role_for(user, comic):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return (
        ComicRole.objects.filter(comic=comic, user=user)
        .values_list("role", flat=True)
        .first()
    )


def is_owner(user, comic) -> bool:
    return role_for(user, comic) == ComicRole.Role.OWNER


def can_manage_comic(user, comic) -> bool:
    return is_owner(user, comic)


def can_edit(user, comic) -> bool:
    return role_for(user, comic) in {ComicRole.Role.OWNER, ComicRole.Role.EDITOR}


def can_publish(user, comic) -> bool:
    return can_edit(user, comic)


def can_moderate(user, comic) -> bool:
    return role_for(user, comic) in {
        ComicRole.Role.OWNER,
        ComicRole.Role.EDITOR,
        ComicRole.Role.MODERATOR,
    }


def can_contribute(user, comic) -> bool:
    return role_for(user, comic) is not None
