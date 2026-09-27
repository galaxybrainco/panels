import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

PASSWORD = "a-strong-passphrase-42"


def _user():
    return get_user_model().objects.create_user(
        email="reader@example.com", password=PASSWORD
    )


def test_mfa_types_and_passkey_login_are_configured():
    assert set(settings.MFA_SUPPORTED_TYPES) >= {"totp", "webauthn", "recovery_codes"}
    assert settings.MFA_PASSKEY_LOGIN_ENABLED is True


@pytest.mark.django_db
def test_mfa_index_requires_login(client):
    response = client.get(reverse("mfa_index"))
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


@pytest.mark.django_db
def test_mfa_index_renders_for_logged_in_user(client):
    client.force_login(_user())
    response = client.get(reverse("mfa_index"))
    assert response.status_code == 200
    assert (
        b"Two-Factor" in response.content or b"two-factor" in response.content.lower()
    )


@pytest.mark.django_db
def test_totp_activation_requires_reauthentication(client):
    client.force_login(_user())
    response = client.get(reverse("mfa_activate_totp"))
    assert response.status_code == 302
    assert "/accounts/reauthenticate/" in response.url


@pytest.mark.django_db
def test_totp_activation_page_renders_after_reauthentication(client):
    client.force_login(_user())
    reauth = client.post(reverse("account_reauthenticate"), {"password": PASSWORD})
    assert reauth.status_code == 302
    response = client.get(reverse("mfa_activate_totp"))
    assert response.status_code == 200
