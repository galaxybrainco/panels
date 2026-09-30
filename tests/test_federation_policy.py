import pytest

from actors.models import Instance
from federation.activitypub import PUBLIC
from federation.policy import delivery_allowed, domain_blocked, domain_silenced


@pytest.mark.django_db
def test_unknown_domain_is_allowed():
    assert delivery_allowed({"to": [PUBLIC]}, "https://other.test/inbox") is True


@pytest.mark.django_db
def test_blocked_domain_is_denied():
    Instance.objects.create(domain="bad.test", blocked=True)
    assert domain_blocked("bad.test") is True
    assert delivery_allowed({"to": [PUBLIC]}, "https://bad.test/inbox") is False


@pytest.mark.django_db
def test_silenced_domain_withholds_public_but_allows_directed():
    Instance.objects.create(domain="quiet.test", silenced=True)
    assert domain_silenced("quiet.test") is True
    assert delivery_allowed({"to": [PUBLIC]}, "https://quiet.test/inbox") is False
    assert (
        delivery_allowed(
            {"to": ["https://quiet.test/u/bob"]}, "https://quiet.test/inbox"
        )
        is True
    )
