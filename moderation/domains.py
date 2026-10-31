from actors.models import Instance
from moderation.reports import NotPermittedError, record_action
from moderation.services import InvalidDomainError, normalize_domain


class InvalidActionError(Exception):
    pass


ACTIONS = {
    "suspend": ("blocked", True, "domain.suspended"),
    "silence": ("silenced", True, "domain.silenced"),
    "reject_media": ("reject_media", True, "domain.reject_media"),
    "reject_reports": ("reject_reports", True, "domain.reject_reports"),
    "allowlist": ("allowlisted", True, "domain.allowlisted"),
    "restore": (None, False, "domain.restored"),
}


def _instance(domain, create=True):
    host = normalize_domain(domain)
    if not host:
        raise InvalidDomainError
    if create:
        instance, _ = Instance.objects.get_or_create(domain=host)
        return instance
    return Instance.objects.filter(domain=host).first()


def domain_policy(domain):
    instance = _instance(domain, create=False)
    if instance is None:
        return None
    return {
        "domain": instance.domain,
        "blocked": instance.blocked,
        "silenced": instance.silenced,
        "reject_media": instance.reject_media,
        "reject_reports": instance.reject_reports,
        "allowlisted": instance.allowlisted,
    }


def apply_domain_action(domain, staff, action):
    if staff is None or not staff.is_staff:
        raise NotPermittedError
    if action not in ACTIONS:
        raise InvalidActionError
    instance = _instance(domain)
    field, value, audit_action = ACTIONS[action]
    if action == "restore":
        instance.blocked = False
        instance.silenced = False
        instance.reject_media = False
        instance.reject_reports = False
        instance.allowlisted = False
        instance.save(
            update_fields=[
                "blocked",
                "silenced",
                "reject_media",
                "reject_reports",
                "allowlisted",
                "updated_at",
            ]
        )
    else:
        setattr(instance, field, value)
        instance.save(update_fields=[field, "updated_at"])
    record_action(staff, audit_action, target=f"domain:{instance.domain}")
    return instance
