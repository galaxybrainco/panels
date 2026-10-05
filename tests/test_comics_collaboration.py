import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from comics.collaboration import change_role, invite_user, remove_collaborator
from comics.models import ComicRole
from comics.permissions import can_manage_collaborators
from comics.services import create_comic


def _user(email):
    return get_user_model().objects.create_user(email=email, password="x")


@pytest.fixture
def scene():
    owner = _user("owner@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    return owner, comic


@pytest.mark.django_db
def test_can_manage_collaborators_is_owner_only(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    assert can_manage_collaborators(owner, comic) is True
    assert can_manage_collaborators(editor, comic) is False
    assert can_manage_collaborators(None, comic) is False


@pytest.mark.django_db
def test_owner_invites_existing_user_case_insensitive(scene):
    owner, comic = scene
    artist = _user("Artist@Example.com")
    membership = invite_user(owner, comic, "artist@example.com", ComicRole.Role.EDITOR)
    assert membership.comic == comic
    assert membership.user == artist
    assert membership.role == ComicRole.Role.EDITOR


@pytest.mark.django_db
def test_invite_unknown_email_is_rejected(scene):
    owner, comic = scene
    with pytest.raises(ValidationError):
        invite_user(owner, comic, "nobody@example.com", ComicRole.Role.EDITOR)


@pytest.mark.django_db
def test_invite_existing_collaborator_is_rejected(scene):
    owner, comic = scene
    _user("editor@example.com")
    invite_user(owner, comic, "editor@example.com", ComicRole.Role.EDITOR)
    with pytest.raises(ValidationError):
        invite_user(owner, comic, "editor@example.com", ComicRole.Role.MODERATOR)


@pytest.mark.django_db
def test_invite_owner_role_is_rejected(scene):
    owner, comic = scene
    _user("editor@example.com")
    with pytest.raises(ValidationError):
        invite_user(owner, comic, "editor@example.com", ComicRole.Role.OWNER)


@pytest.mark.django_db
def test_non_owner_cannot_invite(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    outsider = _user("outsider@example.com")
    with pytest.raises(PermissionDenied):
        invite_user(editor, comic, "outsider@example.com", ComicRole.Role.CONTRIBUTOR)
    with pytest.raises(PermissionDenied):
        invite_user(outsider, comic, "outsider@example.com", ComicRole.Role.CONTRIBUTOR)


@pytest.mark.django_db
def test_change_role_updates_membership(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    invite_user(owner, comic, "editor@example.com", ComicRole.Role.EDITOR)
    membership = change_role(owner, comic, editor, ComicRole.Role.MODERATOR)
    assert membership.role == ComicRole.Role.MODERATOR
    assert ComicRole.objects.get(comic=comic, user=editor).role == (
        ComicRole.Role.MODERATOR
    )


@pytest.mark.django_db
def test_change_role_requires_existing_collaborator(scene):
    owner, comic = scene
    stranger = _user("stranger@example.com")
    with pytest.raises(ValidationError):
        change_role(owner, comic, stranger, ComicRole.Role.EDITOR)


@pytest.mark.django_db
def test_change_role_cannot_change_owner(scene):
    owner, comic = scene
    with pytest.raises(ValidationError):
        change_role(owner, comic, owner, ComicRole.Role.EDITOR)


@pytest.mark.django_db
def test_change_role_rejects_owner_role(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    invite_user(owner, comic, "editor@example.com", ComicRole.Role.EDITOR)
    with pytest.raises(ValidationError):
        change_role(owner, comic, editor, ComicRole.Role.OWNER)


@pytest.mark.django_db
def test_remove_collaborator_deletes_membership(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    invite_user(owner, comic, "editor@example.com", ComicRole.Role.EDITOR)
    remove_collaborator(owner, comic, editor)
    assert not ComicRole.objects.filter(comic=comic, user=editor).exists()


@pytest.mark.django_db
def test_remove_owner_is_rejected(scene):
    owner, comic = scene
    with pytest.raises(ValidationError):
        remove_collaborator(owner, comic, owner)


@pytest.mark.django_db
def test_remove_non_collaborator_is_rejected(scene):
    owner, comic = scene
    stranger = _user("stranger@example.com")
    with pytest.raises(ValidationError):
        remove_collaborator(owner, comic, stranger)


@pytest.mark.django_db
def test_non_owner_cannot_change_or_remove(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    contributor = _user("contributor@example.com")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    with pytest.raises(PermissionDenied):
        change_role(editor, comic, contributor, ComicRole.Role.MODERATOR)
    with pytest.raises(PermissionDenied):
        remove_collaborator(editor, comic, contributor)
