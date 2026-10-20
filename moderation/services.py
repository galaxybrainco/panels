import re
from urllib.parse import urlsplit

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from comics.models import Comic, ComicRole
from moderation.models import DomainBlock, KeywordFilter, Mute, ThreadMute

DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)


class InvalidExpiryError(Exception):
    pass


class InvalidPhraseError(Exception):
    pass


class InvalidDomainError(Exception):
    pass


class InvalidPayloadError(Exception):
    pass


class SelfMuteError(Exception):
    pass


class NotActorOwnerError(Exception):
    pass


class FollowNotFoundError(Exception):
    pass


def _active(queryset):
    now = timezone.now()
    return queryset.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def _validate_expiry(expires_at):
    if expires_at is None:
        return
    if timezone.is_naive(expires_at) or expires_at <= timezone.now():
        raise InvalidExpiryError


def can_manage_actor(user, actor):
    if user is None or actor is None:
        return False
    if actor.user_id == user.id:
        return True
    return Comic.objects.filter(
        actor=actor, roles__user=user, roles__role=ComicRole.Role.OWNER
    ).exists()


def active_mute(user, actor):
    if user is None or actor is None:
        return None
    return _active(Mute.objects.filter(user=user, actor=actor)).first()


def is_muted(user, actor):
    return active_mute(user, actor) is not None


def mute_actor(user, actor, *, hide_notifications=False, expires_at=None):
    if can_manage_actor(user, actor):
        raise SelfMuteError
    _validate_expiry(expires_at)
    mute, _ = Mute.objects.update_or_create(
        user=user,
        actor=actor,
        defaults={
            "hide_notifications": hide_notifications,
            "expires_at": expires_at,
        },
    )
    return mute


def unmute_actor(user, actor):
    return Mute.objects.filter(user=user, actor=actor).delete()[0]


def mute_thread(user, page):
    mute, _ = ThreadMute.objects.get_or_create(user=user, page=page)
    return mute


def unmute_thread(user, page):
    return ThreadMute.objects.filter(user=user, page=page).delete()[0]


def is_thread_muted(user, page):
    if user is None or page is None:
        return False
    return ThreadMute.objects.filter(user=user, page=page).exists()


def add_filter(user, phrase, *, expires_at=None):
    if not isinstance(phrase, str):
        raise InvalidPhraseError
    phrase = phrase.strip()
    if not phrase or len(phrase) > 255:
        raise InvalidPhraseError
    _validate_expiry(expires_at)
    keyword, _ = KeywordFilter.objects.update_or_create(
        user=user, phrase=phrase, defaults={"expires_at": expires_at}
    )
    return keyword


def remove_filter(user, filter_id):
    return KeywordFilter.objects.filter(user=user, pk=filter_id).delete()[0]


def active_filters(user):
    return _active(KeywordFilter.objects.filter(user=user)).order_by("created_at")


def _filter_texts(actor=None, page=None, comment=None):
    texts = []
    if comment is not None:
        texts.append(comment.content)
    if page is not None:
        texts.append(page.title)
        texts.append(page.series.title)
        texts.append(page.series.comic.title)
    if actor is not None:
        texts.append(actor.name)
        texts.append(actor.handle)
    return [text.casefold() for text in texts if text]


def matching_filter(user, *, actor=None, page=None, comment=None):
    if user is None:
        return None
    texts = _filter_texts(actor=actor, page=page, comment=comment)
    if not texts:
        return None
    for keyword in active_filters(user):
        phrase = keyword.phrase.casefold()
        if phrase and any(phrase in text for text in texts):
            return keyword
    return None


def is_filtered(user, *, actor=None, page=None, comment=None):
    return matching_filter(user, actor=actor, page=page, comment=comment) is not None


def _host(domain):
    raw = (domain or "").strip().lower().rstrip(".")
    if not raw:
        return ""
    parsed = urlsplit(f"//{raw}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    if parsed.path not in ("", "/"):
        return ""
    host = parsed.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    if not host or len(host) > 255 or not DOMAIN_RE.match(host):
        return ""
    return host


def block_domain(user, domain):
    host = _host(domain)
    if not host or host == _host(settings.INSTANCE_DOMAIN):
        raise InvalidDomainError
    block, _ = DomainBlock.objects.get_or_create(user=user, domain=host)
    return block


def unblock_domain(user, domain):
    host = _host(domain)
    if not host:
        return 0
    return DomainBlock.objects.filter(user=user, domain=host).delete()[0]


def is_domain_blocked(user, domain):
    if user is None:
        return False
    host = _host(domain)
    if not host:
        return False
    return DomainBlock.objects.filter(user=user, domain=host).exists()


def notification_allowed(user, *, actor=None, page=None, comment=None):
    if user is None:
        return True
    mute = active_mute(user, actor)
    if mute is not None and mute.hide_notifications:
        return False
    if is_thread_muted(user, page):
        return False
    if actor is not None and actor.domain and is_domain_blocked(user, actor.domain):
        return False
    if matching_filter(user, actor=actor, page=page, comment=comment) is not None:
        return False
    return True


def remove_follower(user, actor, follow):
    if not can_manage_actor(user, actor):
        raise NotActorOwnerError
    if follow.target_id != actor.id:
        raise FollowNotFoundError
    follow.delete()
    return True
