import logging
from datetime import datetime

import requests
from django.utils import timezone

from memberships.models import ProviderSupporter
from memberships.services import apply_supporter_entitlement, normalize_email

API_BASE = "https://developers.buymeacoffee.com/api/v1"
TIMEOUT = 15
MAX_PAGES = 20

logger = logging.getLogger(__name__)


class BmacError(Exception):
    pass


def fetch_subscriptions(api_key):
    url = f"{API_BASE}/subscriptions"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    subscriptions = []
    visited = set()
    for _ in range(MAX_PAGES):
        if url in visited:
            raise BmacError("pagination cycle")
        visited.add(url)
        try:
            response = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise BmacError(str(exc)) from exc
        if response.status_code != 200:
            raise BmacError(f"HTTP {response.status_code}")
        try:
            document = response.json()
        except ValueError as exc:
            raise BmacError("invalid JSON") from exc
        data = document.get("data") if isinstance(document, dict) else None
        if not isinstance(data, list):
            raise BmacError("invalid payload")
        subscriptions.extend(data)
        url = document.get("next_page_url") or document.get("next")
        if not url:
            return subscriptions
    raise BmacError(f"subscription pagination exceeded {MAX_PAGES} pages")


def _is_active(sub):
    return str(sub.get("status") or "").lower() == "active"


def _valid_until(sub):
    raw = sub.get("current_period_end")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Invalid BMAC current_period_end: %r", raw)
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def reconcile_bmac(connection):
    subscriptions = fetch_subscriptions(connection.get_api_key())
    active = {}
    now = timezone.now()
    for sub in subscriptions:
        if not isinstance(sub, dict) or not _is_active(sub):
            continue
        email = normalize_email(sub.get("email"))
        if not email:
            continue
        valid_until = _valid_until(sub)
        if valid_until is None or valid_until <= now:
            continue
        active[email] = {
            "tier_name": str(sub.get("membership_level_name") or "").strip(),
            "valid_until": valid_until,
        }
    for email, data in active.items():
        supporter, _ = ProviderSupporter.objects.get_or_create(
            connection=connection, email=email
        )
        if data["tier_name"]:
            supporter.tier_name = data["tier_name"]
        supporter.active = True
        if data["valid_until"] is not None:
            supporter.valid_until = data["valid_until"]
        supporter.save()
    ProviderSupporter.objects.filter(connection=connection, active=True).exclude(
        email__in=active
    ).update(active=False)
    for supporter in connection.supporters.all():
        apply_supporter_entitlement(supporter)
    return {"active": len(active)}
