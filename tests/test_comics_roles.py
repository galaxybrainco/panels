import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from comics.models import ComicRole
from comics.permissions import (
    can_contribute,
    can_edit,
    can_manage_comic,
    can_moderate,
    can_publish,
    is_owner,
    role_for,
)
from comics.services import create_comic


def _user(email):
    return get_user_model().objects.create_user(email=email, password="x")


@pytest.mark.django_db
def test_create_comic_makes_creator_owner():
    owner = _user("owner@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    assert is_owner(owner, comic) is True
    assert can_manage_comic(owner, comic) is True
    assert can_publish(owner, comic) is True
    assert can_moderate(owner, comic) is True


@pytest.mark.django_db
def test_only_one_owner_per_comic():
    owner = _user("owner@example.com")
    other = _user("other@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    with pytest.raises(IntegrityError), transaction.atomic():
        ComicRole.objects.create(comic=comic, user=other, role=ComicRole.Role.OWNER)


@pytest.mark.django_db
def test_user_has_one_role_per_comic():
    owner = _user("owner@example.com")
    editor = _user("editor@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    with pytest.raises(IntegrityError), transaction.atomic():
        ComicRole.objects.create(
            comic=comic, user=editor, role=ComicRole.Role.MODERATOR
        )


@pytest.mark.django_db
def test_role_capabilities():
    owner = _user("owner@example.com")
    editor = _user("editor@example.com")
    contributor = _user("contributor@example.com")
    moderator = _user("moderator@example.com")
    outsider = _user("outsider@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    ComicRole.objects.create(comic=comic, user=moderator, role=ComicRole.Role.MODERATOR)

    assert can_edit(editor, comic) is True
    assert can_publish(editor, comic) is True
    assert can_edit(contributor, comic) is False
    assert can_publish(contributor, comic) is False
    assert can_contribute(contributor, comic) is True
    assert can_moderate(moderator, comic) is True
    assert can_edit(moderator, comic) is False
    assert role_for(outsider, comic) is None
    assert can_contribute(outsider, comic) is False
