from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from comics import permissions
from comics.models import ComicRole

ASSIGNABLE_ROLES = {
    ComicRole.Role.EDITOR,
    ComicRole.Role.CONTRIBUTOR,
    ComicRole.Role.MODERATOR,
}


def _require_owner(user, comic) -> None:
    if not permissions.can_manage_collaborators(user, comic):
        raise PermissionDenied("You cannot manage this comic's collaborators.")


def _lock_roles(comic):
    roles = ComicRole.objects.select_for_update().filter(comic=comic).order_by("id")
    return {membership.user_id: membership for membership in roles}


def _require_locked_owner(roles, user) -> None:
    membership = roles.get(getattr(user, "id", None))
    if membership is None or membership.role != ComicRole.Role.OWNER:
        raise PermissionDenied("You cannot manage this comic's collaborators.")


def _validate_assignable(role):
    if role not in ASSIGNABLE_ROLES:
        raise ValidationError({"role": "Choose editor, contributor, or moderator."})
    return role


@transaction.atomic
def invite_user(owner, comic, email, role):
    _require_owner(owner, comic)
    _validate_assignable(role)
    roles = _lock_roles(comic)
    _require_locked_owner(roles, owner)
    normalized = (email or "").strip()
    if not normalized:
        raise ValidationError({"email": "An email address is required."})
    matches = list(get_user_model().objects.filter(email__iexact=normalized))
    if len(matches) != 1:
        raise ValidationError({"email": "No local account uses that email address."})
    user = matches[0]
    if user.id in roles:
        raise ValidationError({"email": "That user already has a role on this comic."})
    try:
        return ComicRole.objects.create(comic=comic, user=user, role=role)
    except IntegrityError as exc:
        raise ValidationError(
            {"email": "That user already has a role on this comic."}
        ) from exc


@transaction.atomic
def change_role(owner, comic, user, role):
    _require_owner(owner, comic)
    _validate_assignable(role)
    roles = _lock_roles(comic)
    _require_locked_owner(roles, owner)
    membership = roles.get(getattr(user, "id", None))
    if membership is None:
        raise ValidationError(
            {"user": "That user is not a collaborator on this comic."}
        )
    if membership.role == ComicRole.Role.OWNER:
        raise ValidationError(
            {"user": "Transfer ownership to change the owner's role."}
        )
    membership.role = role
    membership.save(update_fields=["role"])
    return membership


@transaction.atomic
def remove_collaborator(owner, comic, user):
    _require_owner(owner, comic)
    roles = _lock_roles(comic)
    _require_locked_owner(roles, owner)
    membership = roles.get(getattr(user, "id", None))
    if membership is None:
        raise ValidationError(
            {"user": "That user is not a collaborator on this comic."}
        )
    if membership.role == ComicRole.Role.OWNER:
        raise ValidationError({"user": "Transfer ownership before removing the owner."})
    membership.delete()


@transaction.atomic
def transfer_ownership(owner, comic, to_user):
    _require_owner(owner, comic)
    roles = _lock_roles(comic)
    _require_locked_owner(roles, owner)
    if to_user is None or getattr(to_user, "id", None) is None:
        raise ValidationError({"user": "Choose a collaborator to receive ownership."})
    if to_user == owner:
        raise ValidationError({"user": "You already own this comic."})
    target = roles.get(to_user.id)
    if target is None:
        raise ValidationError(
            {"user": "Ownership can only be transferred to an existing collaborator."}
        )
    current = roles[owner.id]
    current.role = ComicRole.Role.EDITOR
    current.save(update_fields=["role"])
    target.role = ComicRole.Role.OWNER
    target.save(update_fields=["role"])
    return target
