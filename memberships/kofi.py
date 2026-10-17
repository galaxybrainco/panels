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
    return hmac.compare_digest(stored.encode(), str(token).encode())


def _is_subscription(payload):
    value = payload.get("is_subscription_payment")
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes"}


def _resolve_tier(comic, name, link):
    if name:
        tier = Tier.objects.filter(comic=comic, name__iexact=name).first()
        if tier is not None:
            return tier
    if link is not None:
        latest = (
            SupporterStatus.objects.filter(link=link).order_by("-created_at").first()
        )
        if latest is not None:
            return latest.tier
    return None


def _apply_status(link, tier, valid_until):
    SupporterStatus.objects.filter(link=link, active=True).exclude(tier=tier).update(
        active=False
    )
    SupporterStatus.objects.update_or_create(
        link=link, tier=tier, defaults={"active": True, "valid_until": valid_until}
    )


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
    tier = _resolve_tier(comic, supporter.tier_name, link)
    if tier is None:
        return
    _apply_status(link, tier, supporter.valid_until)


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
    if (
        supporter is not None
        and supporter.active
        and supporter.valid_until is not None
        and supporter.valid_until > timezone.now()
    ):
        tier = _resolve_tier(comic, supporter.tier_name, link)
        if tier is not None:
            _apply_status(link, tier, supporter.valid_until)
    return link
