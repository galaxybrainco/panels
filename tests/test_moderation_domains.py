from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from actors.models import Instance
from moderation import domains
from moderation.models import ModerationAudit


def _user(staff=False):
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x", is_staff=staff
    )


@pytest.mark.django_db
def test_domain_actions_set_and_restore_flags():
    staff = _user(staff=True)
    domains.apply_domain_action("Remote.Test", staff, "suspend")
    instance = Instance.objects.get(domain="remote.test")
    assert instance.blocked is True
    domains.apply_domain_action("remote.test", staff, "silence")
    domains.apply_domain_action("remote.test", staff, "reject_media")
    domains.apply_domain_action("remote.test", staff, "reject_reports")
    domains.apply_domain_action("remote.test", staff, "allowlist")
    instance.refresh_from_db()
    assert instance.silenced and instance.reject_media and instance.reject_reports
    assert instance.allowlisted is True
    domains.apply_domain_action("remote.test", staff, "restore")
    instance.refresh_from_db()
    assert not any(
        [
            instance.blocked,
            instance.silenced,
            instance.reject_media,
            instance.reject_reports,
            instance.allowlisted,
        ]
    )
    assert ModerationAudit.objects.filter(action="domain.suspended").count() == 1


@pytest.mark.django_db
def test_domain_validation_and_permissions():
    staff = _user(staff=True)
    with pytest.raises(domains.InvalidDomainError):
        domains.apply_domain_action("testserver", staff, "suspend")
    with pytest.raises(domains.InvalidActionError):
        domains.apply_domain_action("remote.test", staff, "nope")
    with pytest.raises(domains.NotPermittedError):
        domains.apply_domain_action("remote.test", _user(), "suspend")


@pytest.mark.django_db
def test_domain_endpoints(client):
    staff = _user(staff=True)
    client.force_login(_user())
    assert (
        client.post(
            "/moderation/domains/remote.test/action",
            data='{"action": "suspend"}',
            content_type="application/json",
        ).status_code
        == 403
    )
    client.force_login(staff)
    response = client.post(
        "/moderation/domains/remote.test/action",
        data='{"action": "suspend"}',
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["blocked"] is True
    assert client.get("/moderation/domains/remote.test").json()["blocked"] is True
    assert (
        client.post(
            "/moderation/domains/remote.test/action",
            data='{"action": "nope"}',
            content_type="application/json",
        ).status_code
        == 400
    )
