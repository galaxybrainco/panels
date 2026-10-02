import pytest

from actors.services import create_local_actor
from federation.delivery import fan_out, unique_inboxes
from federation.models import Delivery


def test_unique_inboxes_dedupes_preserving_order():
    assert unique_inboxes(["a", "b", "a", "", "b", "c"]) == ["a", "b", "c"]


@pytest.mark.django_db
def test_enqueue_delivery_creates_one_row_per_inbox():
    actor = create_local_actor("alice")
    deliveries = fan_out(
        {"type": "Create"}, ["https://a.test/inbox", "https://a.test/inbox"], actor
    )
    assert len(deliveries) == 1
    assert Delivery.objects.count() == 1
    assert deliveries[0].inbox_url == "https://a.test/inbox"
