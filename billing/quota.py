from django.conf import settings

from billing.objects import get_field


def _items(subscription):
    items = get_field(subscription, "items") or {}
    return get_field(items, "data") or []


def _quantity(item):
    try:
        return int(get_field(item, "quantity") or 0)
    except TypeError, ValueError:
        return 0


def addon_item(subscription):
    price_id = settings.BILLING_STORAGE_ADDON_PRICE
    if not price_id:
        return "", 0
    item_id = ""
    units = 0
    for item in _items(subscription):
        price = get_field(item, "price") or {}
        if get_field(price, "id") != price_id:
            continue
        item_id = item_id or get_field(item, "id") or ""
        units += _quantity(item)
    return item_id, units


def addon_item_ids(subscription):
    price_id = settings.BILLING_STORAGE_ADDON_PRICE
    if not price_id:
        return []
    ids = []
    for item in _items(subscription):
        price = get_field(item, "price") or {}
        if get_field(price, "id") == price_id:
            item_id = get_field(item, "id")
            if item_id:
                ids.append(item_id)
    return ids


def addon_units(subscription):
    return addon_item(subscription)[1]


def quota_bytes_for(subscription):
    return (
        settings.MEDIA_STORAGE_QUOTA_BYTES
        + addon_units(subscription) * settings.BILLING_STORAGE_ADDON_BYTES
    )
