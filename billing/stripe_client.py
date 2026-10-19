import logging

import stripe
from django.conf import settings

logger = logging.getLogger(__name__)


class StripeRequestError(Exception):
    pass


def _run(call, **kwargs):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        return call(**kwargs)
    except stripe.StripeError as exc:
        logger.warning("Stripe request failed: %s", exc)
        raise StripeRequestError(str(exc)) from exc


def create_customer(user):
    return _run(
        stripe.Customer.create,
        email=user.email,
        metadata={"user_id": str(user.id)},
    )


def create_checkout_session(**kwargs):
    return _run(stripe.checkout.Session.create, **kwargs)


def create_portal_session(**kwargs):
    return _run(stripe.billing_portal.Session.create, **kwargs)
