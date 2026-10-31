import ipaddress

from moderation.models import BlockedEmailDomain, BlockedIP
from moderation.reports import NotPermittedError, record_action
from moderation.services import InvalidDomainError, normalize_domain


class InvalidAddressError(Exception):
    pass


def _require_staff(staff):
    if staff is None or not staff.is_staff:
        raise NotPermittedError


def add_email_domain(domain, staff):
    _require_staff(staff)
    host = normalize_domain(domain)
    if not host:
        raise InvalidDomainError
    row, created = BlockedEmailDomain.objects.get_or_create(domain=host)
    if created:
        record_action(staff, "registration.email_domain_added", target=f"email:{host}")
    return row


def remove_email_domain(domain, staff):
    _require_staff(staff)
    host = normalize_domain(domain)
    if not host:
        raise InvalidDomainError
    deleted, _ = BlockedEmailDomain.objects.filter(domain=host).delete()
    if deleted:
        record_action(
            staff, "registration.email_domain_removed", target=f"email:{host}"
        )
    return bool(deleted)


def email_domains():
    return list(BlockedEmailDomain.objects.order_by("domain"))


def email_domain_blocked(email):
    if not isinstance(email, str) or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    return BlockedEmailDomain.objects.filter(domain=domain).exists()


def _network(address):
    try:
        return ipaddress.ip_network(str(address).strip(), strict=False)
    except (ValueError, TypeError) as exc:
        raise InvalidAddressError from exc


def add_ip(address, staff):
    _require_staff(staff)
    network = _network(address)
    value = str(network)
    row, created = BlockedIP.objects.get_or_create(address=value)
    if created:
        record_action(staff, "registration.ip_added", target=f"ip:{value}")
    return row


def remove_ip(address, staff):
    _require_staff(staff)
    network = _network(address)
    value = str(network)
    deleted, _ = BlockedIP.objects.filter(address=value).delete()
    if deleted:
        record_action(staff, "registration.ip_removed", target=f"ip:{value}")
    return bool(deleted)


def ips():
    return list(BlockedIP.objects.order_by("address"))


def ip_blocked(address):
    try:
        ip = ipaddress.ip_address(str(address))
    except ValueError:
        return False
    return any(ip in _network(row.address) for row in BlockedIP.objects.all())
