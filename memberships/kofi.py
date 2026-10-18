import hmac
from datetime import timedelta

from django.utils import timezone

from memberships.models import ProviderConnection, ProviderSupporter
from memberships.services import (
    apply_supporter_entitlement,
    link_supporter,
    normalize_email,
)

SUBSCRIPTION_PERIOD = timedelta(days=30)
GRACE_PERIOD = timedelta(days=3)


def verify_token(connection, token):
    stored = connection.get_webhook_token()
    if not stored or not token:
        return False
    return hmac.compare_digest(stored.encode(), str(token).encode())


def _is_subscription(payload):
    value = payload.get("is_subscription_payment")
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes"}


def process_kofi_event(connection, payload):
    if not verify_token(connection, payload.get("verification_token")):
        return None
    email = normalize_email(payload.get("email"))
    if not email:
        return None
    transaction_id = str(payload.get("kofi_transaction_id") or "").strip()
    supporter, _ = ProviderSupporter.objects.get_or_create(
        connection=connection, email=email
    )
    if transaction_id and supporter.last_transaction_id == transaction_id:
        return supporter
    now = timezone.now()
    supporter.last_payment_at = now
    if _is_subscription(payload):
        tier_name = str(payload.get("tier_name") or "").strip()
        if tier_name:
            supporter.tier_name = tier_name
        supporter.active = True
        supporter.valid_until = now + SUBSCRIPTION_PERIOD + GRACE_PERIOD
    if transaction_id:
        supporter.last_transaction_id = transaction_id
    supporter.save()
    apply_supporter_entitlement(supporter)
    return supporter


def attach_pending_supporter(actor, comic, provider, email):
    normalized = normalize_email(email)
    user_obj = getattr(actor, "user", None)
    if user_obj is None or user_obj.email.strip().lower() != normalized:
        return None
    link = link_supporter(actor, comic, provider, normalized)
    connection = ProviderConnection.objects.filter(
        comic=comic, provider=provider
    ).first()
    if connection is not None:
        supporter = ProviderSupporter.objects.filter(
            connection=connection, email=normalized
        ).first()
        if (
            supporter is not None
            and supporter.active
            and supporter.valid_until is not None
            and supporter.valid_until > timezone.now()
        ):
            apply_supporter_entitlement(supporter)
    return link
