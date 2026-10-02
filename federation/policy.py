from urllib.parse import urlparse

from actors.models import Instance
from federation.activitypub import is_public


def _instance(domain):
    return Instance.objects.filter(domain=domain).first()


def domain_blocked(domain) -> bool:
    instance = _instance(domain)
    return bool(instance and instance.blocked)


def domain_silenced(domain) -> bool:
    instance = _instance(domain)
    return bool(instance and instance.silenced)


def delivery_allowed(activity, inbox_url) -> bool:
    domain = urlparse(inbox_url).netloc
    if domain_blocked(domain):
        return False
    if domain_silenced(domain) and is_public(activity):
        return False
    return True
