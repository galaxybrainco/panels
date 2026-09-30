import pytest
from django.db import IntegrityError, transaction

from actors.services import create_local_actor
from federation.models import (
    Delivery,
    DeliveryStatus,
    PeerSignaturePreference,
    SignatureScheme,
)


@pytest.mark.django_db
def test_delivery_defaults():
    actor = create_local_actor("alice")
    delivery = Delivery.objects.create(
        inbox_url="https://other.test/inbox",
        activity={"type": "Create"},
        actor=actor,
    )
    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.attempts == 0
    assert delivery.max_attempts == 6
    assert delivery.next_attempt_at is None


@pytest.mark.django_db
def test_peer_signature_preference_domain_is_unique():
    PeerSignaturePreference.objects.create(
        domain="other.test", scheme=SignatureScheme.CAVAGE
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PeerSignaturePreference.objects.create(
            domain="other.test", scheme=SignatureScheme.RFC9421
        )
