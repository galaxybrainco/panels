from django.conf import settings
from django.db import IntegrityError

from billing import quota, stripe_client
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


class InvalidUnitsError(Exception):
    pass


class AddonsUnavailableError(Exception):
    pass


class NoSubscriptionError(Exception):
    pass


class StripeError(Exception):
    pass


def is_owner(comic, user):
    return comic.roles.filter(user=user, role=ComicRole.Role.OWNER).exists()


def get_or_create_customer(user):
    customer = stripe_client.create_customer(user)
    try:
        account, created = BillingCustomer.objects.get_or_create(
            user=user, defaults={"stripe_customer_id": customer.id}
        )
    except IntegrityError:
        account = BillingCustomer.objects.get(user=user)
        created = False
    if not created and not account.stripe_customer_id:
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


def validate_units(units):
    if isinstance(units, bool) or not isinstance(units, int):
        raise InvalidUnitsError
    if units < 0 or units > settings.BILLING_STORAGE_ADDON_MAX_UNITS:
        raise InvalidUnitsError
    return units


def start_checkout(comic, user, interval="month", addon_units=0):
    if not settings.BILLING_ENABLED:
        raise BillingDisabledError
    if not is_owner(comic, user):
        raise NotOwnerError
    addon_units = validate_units(addon_units)
    if addon_units and not settings.BILLING_STORAGE_ADDON_PRICE:
        raise AddonsUnavailableError
    if ComicPlan.objects.filter(comic=comic, status=PlanStatus.ACTIVE).exists():
        raise AlreadySubscribedError
    price = price_for(interval)
    line_items = [{"price": price, "quantity": 1}]
    if addon_units:
        line_items.append(
            {"price": settings.BILLING_STORAGE_ADDON_PRICE, "quantity": addon_units}
        )
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
            line_items=line_items,
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


def set_addon_units(comic, user, units):
    if not settings.BILLING_ENABLED:
        raise BillingDisabledError
    if not is_owner(comic, user):
        raise NotOwnerError
    units = validate_units(units)
    price = settings.BILLING_STORAGE_ADDON_PRICE
    if not price:
        raise AddonsUnavailableError
    plan = ComicPlan.objects.filter(comic=comic).first()
    if (
        plan is None
        or not plan.stripe_subscription_id
        or plan.status not in (PlanStatus.ACTIVE, PlanStatus.PAST_DUE)
    ):
        raise NoSubscriptionError
    item_id = plan.stripe_addon_item_id
    try:
        if not item_id:
            subscription = stripe_client.retrieve_subscription(
                plan.stripe_subscription_id
            )
            item_id, _ = quota.addon_item(subscription)
            if item_id:
                plan.stripe_addon_item_id = item_id
                plan.save(update_fields=["stripe_addon_item_id"])
        if units == 0:
            if item_id:
                stripe_client.delete_subscription_item(item_id)
        elif item_id:
            stripe_client.modify_subscription_item(item_id, quantity=units)
        else:
            stripe_client.create_subscription_item(
                subscription=plan.stripe_subscription_id,
                price=price,
                quantity=units,
            )
    except stripe_client.StripeRequestError as exc:
        raise StripeError(str(exc)) from exc
    return units
