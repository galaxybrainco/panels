import pytest
from django.db import IntegrityError, transaction

from actors.models import Instance


def test_instance_domain_is_unique(db):
    Instance.objects.create(domain="other.test")
    with pytest.raises(IntegrityError), transaction.atomic():
        Instance.objects.create(domain="other.test")
