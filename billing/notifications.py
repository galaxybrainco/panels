import logging

from django.conf import settings
from django.core.mail import send_mail

from comics.models import ComicRole

logger = logging.getLogger(__name__)


def _owner_email(comic):
    role = comic.roles.filter(role=ComicRole.Role.OWNER).select_related("user").first()
    if role is None:
        return ""
    return role.user.email or ""


def _send(plan, subject, body):
    email = _owner_email(plan.comic)
    if not email:
        return False
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email])
    except Exception:
        logger.exception("Failed to send billing notice for plan %s", plan.pk)
        return False
    return True


def notify_past_due(plan, grace_until):
    subject = f"Payment failed for {plan.comic.title}"
    body = (
        f"The plan for {plan.comic.title} could not be renewed.\n"
        f"Uploads stay enabled until {grace_until:%Y-%m-%d %H:%M} UTC.\n"
        f"Update billing: {settings.INSTANCE_URL}\n"
    )
    return _send(plan, subject, body)


def notify_frozen(plan):
    subject = f"{plan.comic.title} is read-only"
    body = (
        f"The plan for {plan.comic.title} is frozen: new uploads are disabled.\n"
        "The comic still serves and federates existing pages.\n"
        f"Update billing: {settings.INSTANCE_URL}\n"
    )
    return _send(plan, subject, body)


def notify_restored(plan):
    subject = f"{plan.comic.title} is active again"
    body = (
        f"The plan for {plan.comic.title} is active again and uploads work.\n"
        f"Manage billing: {settings.INSTANCE_URL}\n"
    )
    return _send(plan, subject, body)
