import logging

import stripe
from django.conf import settings

logger = logging.getLogger(__name__)


class StripeRequestError(Exception):
    pass


def _run(call, *args, **kwargs):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        return call(*args, **kwargs)
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


class InvalidSignatureError(Exception):
    pass


def construct_event(payload, signature, secret):
    if not secret:
        raise InvalidSignatureError("missing webhook secret")
    try:
        return stripe.Webhook.construct_event(payload, signature, secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise InvalidSignatureError(str(exc)) from exc


def retrieve_subscription(subscription_id):
    return _run(stripe.Subscription.retrieve, subscription_id)


def create_subscription_item(**kwargs):
    return _run(stripe.SubscriptionItem.create, **kwargs)


def modify_subscription_item(item_id, **kwargs):
    return _run(stripe.SubscriptionItem.modify, item_id, **kwargs)


def delete_subscription_item(item_id):
    return _run(stripe.SubscriptionItem.delete, item_id)
