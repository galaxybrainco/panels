from federation.activitypub import is_public


def _instance(domain):
    from moderation import accounts

    return accounts.domain_instance(domain)


def domain_blocked(domain) -> bool:
    instance = _instance(domain)
    return bool(instance and instance.blocked)


def domain_silenced(domain) -> bool:
    instance = _instance(domain)
    return bool(instance and instance.silenced)


def delivery_allowed(activity, inbox_url) -> bool:
    from moderation import accounts

    domain = accounts.domain_host(inbox_url)
    if domain_blocked(domain):
        return False
    if domain_silenced(domain) and is_public(activity):
        return False
    return True
