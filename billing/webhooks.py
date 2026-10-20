from datetime import UTC, datetime
from uuid import UUID

from billing import lifecycle, quota
from billing.models import ComicPlan, PlanStatus
from billing.objects import get_field
from comics.models import Comic

SUBSCRIPTION_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}

PAYMENT_EVENTS = {"invoice.payment_failed"}

ACTIVE_STATUSES = {"active", "trialing"}


_get = get_field


def plan_status_for(status):
    if str(status or "").lower() in ACTIVE_STATUSES:
        return PlanStatus.ACTIVE
    return PlanStatus.PAST_DUE


def _period_end(subscription):
    raw = _get(subscription, "current_period_end")
    if raw is None:
        items = _get(subscription, "items") or {}
        for item in _get(items, "data") or []:
            value = _get(item, "current_period_end")
            if value is not None:
                raw = value
                break
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except TypeError, ValueError, OSError, OverflowError:
        return None


def sync_subscription(subscription):
    metadata = _get(subscription, "metadata") or {}
    try:
        comic_id = UUID(str(_get(metadata, "comic_id")))
    except TypeError, ValueError:
        return None
    comic = Comic.objects.filter(pk=comic_id).first()
    if comic is None:
        return None
    customer = _get(subscription, "customer")
    if not isinstance(customer, str):
        customer = _get(customer, "id")
    subscription_id = _get(subscription, "id")
    period_end = _period_end(subscription)
    status = plan_status_for(_get(subscription, "status"))
    addon_item_id, _ = quota.addon_item(subscription)
    plan_quota = quota.quota_bytes_for(subscription)
    plan, created = ComicPlan.objects.get_or_create(
        comic=comic,
        defaults={
            "quota_bytes": plan_quota,
            "stripe_addon_item_id": addon_item_id,
            "stripe_customer_id": customer or "",
            "stripe_subscription_id": subscription_id or "",
            "current_period_end": period_end,
        },
    )
    if not created:
        plan.quota_bytes = plan_quota
        plan.stripe_addon_item_id = addon_item_id
        plan.stripe_customer_id = customer or plan.stripe_customer_id
        plan.stripe_subscription_id = subscription_id or plan.stripe_subscription_id
        if period_end is not None:
            plan.current_period_end = period_end
        plan.save(
            update_fields=[
                "quota_bytes",
                "stripe_addon_item_id",
                "stripe_customer_id",
                "stripe_subscription_id",
                "current_period_end",
            ]
        )
    lifecycle.apply_status(plan, status)
    return plan


def _invoice_subscription(invoice):
    subscription = _get(invoice, "subscription")
    if subscription is None:
        parent = _get(invoice, "parent") or {}
        details = _get(parent, "subscription_details") or {}
        subscription = _get(details, "subscription")
    if not isinstance(subscription, str):
        subscription = _get(subscription, "id")
    return subscription


def handle_payment_failed(invoice):
    subscription = _invoice_subscription(invoice)
    if not subscription:
        return None
    plan = ComicPlan.objects.filter(stripe_subscription_id=subscription).first()
    if plan is None:
        return None
    lifecycle.apply_status(plan, PlanStatus.PAST_DUE)
    return plan
