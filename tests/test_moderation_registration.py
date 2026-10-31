from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from moderation import registration
from moderation.models import BlockedEmailDomain, ModerationAudit


def _user(staff=False):
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x", is_staff=staff
    )


@pytest.mark.django_db
def test_email_domain_blocks_are_exact_and_case_insensitive():
    staff = _user(staff=True)
    registration.add_email_domain("Spam.Example", staff)
    assert BlockedEmailDomain.objects.get(domain="spam.example")
    assert registration.email_domain_blocked("x@spam.example") is True
    assert registration.email_domain_blocked("x@SPAM.example") is True
    assert registration.email_domain_blocked("x@notspam.example") is False
    registration.remove_email_domain("spam.example", staff)
    assert BlockedEmailDomain.objects.count() == 0
    assert (
        ModerationAudit.objects.filter(action="registration.email_domain_added").count()
        == 1
    )


@pytest.mark.django_db
def test_ip_blocks_support_cidr():
    staff = _user(staff=True)
    registration.add_ip("203.0.113.0/24", staff)
    assert registration.ip_blocked("203.0.113.9") is True
    assert registration.ip_blocked("198.51.100.1") is False
    with pytest.raises(registration.InvalidAddressError):
        registration.add_ip("not-an-ip", staff)


@pytest.mark.django_db
def test_registration_endpoints(client):
    staff = _user(staff=True)
    client.force_login(_user())
    assert (
        client.get("/moderation/registration-blocks/email-domains").status_code == 403
    )
    client.force_login(staff)
    response = client.post(
        "/moderation/registration-blocks/email-domains",
        data='{"domain": "spam.example"}',
        content_type="application/json",
    )
    assert response.status_code == 201
    listing = client.get("/moderation/registration-blocks/email-domains").json()
    assert listing["domains"] == ["spam.example"]
    response = client.delete(
        "/moderation/registration-blocks/email-domains",
        data='{"domain": "spam.example"}',
        content_type="application/json",
    )
    assert response.status_code == 200
    response = client.post(
        "/moderation/registration-blocks/ip-blocks",
        data='{"address": "203.0.113.0/24"}',
        content_type="application/json",
    )
    assert response.status_code == 201
    assert client.get("/moderation/registration-blocks/ip-blocks").json()[
        "addresses"
    ] == ["203.0.113.0/24"]
