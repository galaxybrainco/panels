from django.conf import settings

from billing import stripe_client
from billing.models import BillingCustomer, ComicPlan, PlanStatus
from comics.models import ComicRole


class BillingDisabledError(Exception):
    pass


class NotOwnerError(Exception):
    pass


class AlreadySubscribedError(Exception):
    pass


class InvalidIntervalError(Exception):
    pass


class InvalidPayloadError(Exception):
    pass


class NoBillingAccountError(Exception):
    pass


class StripeError(Exception):
    pass


def is_owner(comic, user):
    return comic.roles.filter(user=user, role=ComicRole.Role.OWNER).exists()


def get_or_create_customer(user):
    customer = stripe_client.create_customer(user)
    account, _ = BillingCustomer.objects.get_or_create(user=user)
    account.stripe_customer_id = customer.id
    account.save(update_fields=["stripe_customer_id"])
    return account


def price_for(interval):
    if interval == "month":
        price = settings.BILLING_PRICE_MONTHLY
    elif interval == "year":
        price = settings.BILLING_PRICE_ANNUAL
    else:
        raise InvalidIntervalError(interval)
    if not price:
        if interval == "year":
            raise InvalidIntervalError(interval)
        raise StripeError("missing monthly price")
    return price


def start_checkout(comic, user, interval="month"):
    if not settings.BILLING_ENABLED:
        raise BillingDisabledError
    if not is_owner(comic, user):
        raise NotOwnerError
    if ComicPlan.objects.filter(comic=comic, status=PlanStatus.ACTIVE).exists():
        raise AlreadySubscribedError
    price = price_for(interval)
    existing = BillingCustomer.objects.filter(user=user).first()
    if existing is not None and existing.stripe_customer_id:
        account = existing
    else:
        try:
            account = get_or_create_customer(user)
        except stripe_client.StripeRequestError as exc:
            raise StripeError(str(exc)) from exc
    try:
        session = stripe_client.create_checkout_session(
            mode="subscription",
            customer=account.stripe_customer_id,
            line_items=[{"price": price, "quantity": 1}],
            success_url=settings.BILLING_SUCCESS_URL,
            cancel_url=settings.BILLING_CANCEL_URL,
            client_reference_id=str(comic.id),
            subscription_data={
                "metadata": {"comic_id": str(comic.id), "user_id": str(user.id)}
            },
        )
    except stripe_client.StripeRequestError as exc:
        raise StripeError(str(exc)) from exc
    return session.url


def open_portal(comic, user):
    if not settings.BILLING_ENABLED:
        raise BillingDisabledError
    if not is_owner(comic, user):
        raise NotOwnerError
    account = BillingCustomer.objects.filter(user=user).first()
    if account is None or not account.stripe_customer_id:
        raise NoBillingAccountError
    try:
        session = stripe_client.create_portal_session(
            customer=account.stripe_customer_id,
            return_url=settings.BILLING_PORTAL_RETURN_URL,
        )
    except stripe_client.StripeRequestError as exc:
        raise StripeError(str(exc)) from exc
    return session.url
