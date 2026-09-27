import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from actors.models import Actor

PASSWORD = "a-strong-passphrase-42"
SIGNUP_DATA = {
    "email": "reader@example.com",
    "password1": PASSWORD,
    "password2": PASSWORD,
    "handle": "alice",
}


def _post_signup(client, **overrides):
    data = {**SIGNUP_DATA, **overrides}
    return client.post(reverse("account_signup"), data)


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=False)
def test_signup_is_closed_when_registration_disabled(client):
    response = client.get(reverse("account_signup"))
    assert response.status_code == 200
    _post_signup(client)
    assert get_user_model().objects.count() == 0


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=True)
def test_signup_creates_user_and_local_actor(client):
    response = _post_signup(client)
    assert response.status_code in (200, 302)
    user = get_user_model().objects.get(email="reader@example.com")
    actor = Actor.objects.get(handle="alice", domain="")
    assert actor.user == user
    assert actor.is_local
    assert actor.public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=True)
def test_signup_normalizes_handle(client):
    _post_signup(client, handle="ALICE")
    assert Actor.objects.filter(handle="alice", domain="").exists()


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=True)
@pytest.mark.parametrize("handle", ["ab", "bad handle", "instance", "admin"])
def test_signup_rejects_invalid_or_reserved_handle(client, handle):
    _post_signup(client, handle=handle)
    assert get_user_model().objects.count() == 0
    assert Actor.objects.count() == 0


@pytest.mark.django_db
@override_settings(INSTANCE_OPEN_REGISTRATIONS=True)
def test_signup_rejects_taken_handle(client):
    from actors.services import create_local_actor

    create_local_actor("alice")
    _post_signup(client)
    assert get_user_model().objects.count() == 0
