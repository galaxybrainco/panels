from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from billing.models import BillingCustomer


def _user():
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


@pytest.mark.django_db
def test_billing_customer_is_one_per_user_with_unique_stripe_id():
    user = _user()
    BillingCustomer.objects.create(user=user, stripe_customer_id="cus_1")
    assert user.billing_customer.stripe_customer_id == "cus_1"
    other = _user()
    with pytest.raises(IntegrityError), transaction.atomic():
        BillingCustomer.objects.create(user=other, stripe_customer_id="cus_1")
    with pytest.raises(IntegrityError), transaction.atomic():
        BillingCustomer.objects.create(user=user, stripe_customer_id="cus_2")
