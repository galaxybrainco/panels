import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction


def test_user_model_uses_email_as_identifier():
    assert get_user_model().USERNAME_FIELD == "email"


def test_create_user_uses_email_as_identifier(db):
    user = get_user_model().objects.create_user(
        email="reader@example.com", password="s3cret-pass"
    )
    assert user.email == "reader@example.com"
    assert user.check_password("s3cret-pass")
    assert user.is_active is True
    assert user.is_staff is False
    assert user.is_superuser is False


def test_create_user_requires_email(db):
    with pytest.raises(ValueError):
        get_user_model().objects.create_user(email="", password="x")


def test_create_superuser_sets_flags(db):
    user = get_user_model().objects.create_superuser(
        email="admin@example.com", password="s3cret-pass"
    )
    assert user.is_staff is True
    assert user.is_superuser is True


def test_email_is_unique(db):
    get_user_model().objects.create_user(email="dup@example.com", password="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        get_user_model().objects.create_user(email="dup@example.com", password="x")
