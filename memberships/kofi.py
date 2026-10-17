import hmac
from datetime import timedelta

from django.utils import timezone

from memberships.models import (
    ProviderConnection,
    ProviderSupporter,
    SupporterStatus,
    Tier,
)
from memberships.services import link_supporter

SUBSCRIPTION_PERIOD = timedelta(days=30)
GRACE_PERIOD = timedelta(days=3)


def normalize_email(email):
    return (email or "").strip().lower()


def verify_token(connection, token):
    stored = connection.get_webhook_token()
    if not stored or not token:
        return False
    return hmac.compare_digest(stored, str(token))


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
    supporter, _ = ProviderSupporter.objects.get_or_create(
        connection=connection, email=email
    )
    now = timezone.now()
    supporter.last_payment_at = now
    if _is_subscription(payload):
        supporter.tier_name = str(payload.get("tier_name") or "").strip()
        supporter.active = True
        supporter.valid_until = now + SUBSCRIPTION_PERIOD + GRACE_PERIOD
    supporter.save()
    _refresh_linked_status(supporter)
    return supporter


def _refresh_linked_status(supporter):
    if not supporter.active or supporter.valid_until is None:
        return
    comic = supporter.connection.comic
    link = comic.supporter_links.filter(
        provider=supporter.connection.provider,
        provider_email__iexact=supporter.email,
    ).first()
    if link is None:
        return
    tier = Tier.objects.filter(comic=comic, name=supporter.tier_name).first()
    if tier is None:
        return
    SupporterStatus.objects.create(
        link=link, tier=tier, valid_until=supporter.valid_until
    )


def attach_pending_supporter(actor, comic, provider, email):
    normalized = normalize_email(email)
    user_obj = getattr(actor, "user", None)
    if user_obj is None or user_obj.email.strip().lower() != normalized:
        return None
    link = link_supporter(actor, comic, provider, normalized)
    connection = ProviderConnection.objects.filter(
        comic=comic, provider=provider
    ).first()
    if connection is None:
        return link
    supporter = ProviderSupporter.objects.filter(
        connection=connection, email=normalized
    ).first()
    if supporter is not None and supporter.active and supporter.valid_until is not None:
        tier = Tier.objects.filter(comic=comic, name=supporter.tier_name).first()
        if tier is not None:
            SupporterStatus.objects.create(
                link=link, tier=tier, valid_until=supporter.valid_until
            )
    return link
