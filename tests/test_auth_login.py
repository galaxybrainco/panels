import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.urls import reverse

PASSWORD = "a-strong-passphrase-42"


def _make_user(email, *, verified):
    user = get_user_model().objects.create_user(email=email, password=PASSWORD)
    EmailAddress.objects.create(user=user, email=email, verified=verified, primary=True)
    return user


@pytest.mark.django_db
def test_login_page_renders(client):
    response = client.get(reverse("account_login"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_verified_user_can_log_in_with_email(client):
    _make_user("reader@example.com", verified=True)
    response = client.post(
        reverse("account_login"),
        {"login": "reader@example.com", "password": PASSWORD},
    )
    assert response.status_code == 302
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_unverified_user_cannot_log_in(client):
    _make_user("unverified@example.com", verified=False)
    client.post(
        reverse("account_login"),
        {"login": "unverified@example.com", "password": PASSWORD},
    )
    assert "_auth_user_id" not in client.session
