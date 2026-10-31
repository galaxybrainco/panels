import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q

from actors.models import Actor, Instance
from comics.models import Page
from moderation.models import AccountModeration, AccountState
from moderation.reports import NotPermittedError, record_action

logger = logging.getLogger(__name__)


class InvalidActionError(Exception):
    pass


class LocalOnlyError(Exception):
    pass


LOCAL_ONLY = {"freeze", "disable", "warn"}
AUDIT_ACTIONS = {
    "limit": "account.limited",
    "suspend": "account.suspended",
    "freeze": "account.frozen",
    "disable": "account.disabled",
    "restore": "account.restored",
    "warn": "account.warned",
    "force_sensitive": "account.force_sensitive",
}
STATES = {
    "limit": AccountState.LIMITED,
    "suspend": AccountState.SUSPENDED,
    "freeze": AccountState.FROZEN,
    "disable": AccountState.DISABLED,
    "restore": AccountState.NORMAL,
}


def account_moderation(actor):
    record, _ = AccountModeration.objects.get_or_create(actor=actor)
    return record


def account_state(actor):
    record = AccountModeration.objects.filter(actor=actor).first()
    return record.state if record is not None else AccountState.NORMAL


def is_limited(actor):
    return account_state(actor) == AccountState.LIMITED


def is_suspended(actor):
    return account_state(actor) == AccountState.SUSPENDED


def is_frozen(actor):
    return account_state(actor) == AccountState.FROZEN


def is_disabled(actor):
    return account_state(actor) == AccountState.DISABLED


def is_force_sensitive(user):
    if user is None:
        return False
    record = AccountModeration.objects.filter(actor__user=user).first()
    return record.force_sensitive if record is not None else False


def can_post(user):
    if user is None or not user.is_active:
        return False
    actor = Actor.objects.filter(user=user).first()
    if actor is None:
        return True
    return account_state(actor) not in {
        AccountState.FROZEN,
        AccountState.DISABLED,
        AccountState.SUSPENDED,
    }


def publicly_visible(actor):
    if is_limited(actor) or is_suspended(actor):
        return False
    if actor.domain:
        instance = Instance.objects.filter(domain=actor.domain).first()
        if instance is not None and (instance.blocked or instance.silenced):
            return False
    return True


def allowed_domain(domain):
    if not domain:
        return True
    instance = Instance.objects.filter(domain=domain).first()
    if instance is not None and instance.blocked:
        return False
    if settings.FEDERATION_ALLOWLIST_ENABLED:
        return instance is not None and instance.allowlisted
    return True


def should_drop_inbound(actor):
    return is_suspended(actor) or not allowed_domain(actor.domain)


def _warn(user, reason):
    if not user.email:
        return
    try:
        send_mail(
            "Moderation warning",
            f"A moderator sent you a warning:\n\n{reason}\n",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
        )
    except Exception:
        logger.exception("Failed to send warning to %s", user.pk)


def apply_account_action(actor, staff, action, *, reason, enabled=None):
    if staff is None or not staff.is_staff:
        raise NotPermittedError
    if action not in AUDIT_ACTIONS:
        raise InvalidActionError
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidActionError
    reason = reason.strip()
    if action in LOCAL_ONLY and actor.user is None:
        raise LocalOnlyError
    if action == "force_sensitive" and not isinstance(enabled, bool):
        raise InvalidActionError
    record = account_moderation(actor)
    if action == "force_sensitive":
        record.force_sensitive = enabled
        if enabled and actor.user is not None:
            Page.objects.filter(
                Q(author=actor.user) | Q(published_by=actor.user)
            ).update(sensitive=True)
    else:
        if action == "disable":
            actor.user.is_active = False
            actor.user.save(update_fields=["is_active"])
        elif action == "restore" and actor.user is not None:
            if not actor.user.is_active:
                actor.user.is_active = True
                actor.user.save(update_fields=["is_active"])
        elif action == "warn":
            _warn(actor.user, reason)
        if action in STATES:
            record.state = STATES[action]
    record.reason = reason
    record.changed_by = staff
    record.save()
    record_action(
        staff,
        AUDIT_ACTIONS[action],
        target=f"actor:{actor.id}",
        data={"reason": reason, "enabled": enabled},
    )
    return record
