from django.conf import settings

from billing import stripe_client
from billing.models import BillingCustomer, ComicPlan, PlanStatus
from comics.models import ComicRole


class BillingDisabled(Exception):
    pass


class NotOwner(Exception):
    pass


class AlreadySubscribed(Exception):
    pass


class InvalidInterval(Exception):
    pass


class InvalidPayload(Exception):
    pass


class NoBillingAccount(Exception):
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
        raise InvalidInterval(interval)
    if not price:
        if interval == "year":
            raise InvalidInterval(interval)
        raise StripeError("missing monthly price")
    return price


def start_checkout(comic, user, interval="month"):
    if not settings.BILLING_ENABLED:
        raise BillingDisabled
    if not is_owner(comic, user):
        raise NotOwner
    if ComicPlan.objects.filter(comic=comic, status=PlanStatus.ACTIVE).exists():
        raise AlreadySubscribed
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
