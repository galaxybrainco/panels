from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils.text import slugify

from actors.handles import validate_handle
from actors.models import ActorType
from actors.services import create_local_actor
from comics import permissions
from comics.models import (
    Comic,
    ComicRole,
    ContentRating,
    FederationMode,
    Page,
    PageStatus,
    Series,
)
from federation.activitypub import Audience


@transaction.atomic
def create_comic(
    owner,
    handle,
    title,
    *,
    description="",
    content_rating=ContentRating.ALL_AGES,
    default_audience=Audience.PUBLIC,
    default_federation=FederationMode.FEDERATED,
    update_schedule=None,
    tags=None,
    actor_type=ActorType.PERSON,
):
    slug = validate_handle(handle)
    if Comic.objects.filter(slug=slug).exists():
        raise ValidationError("That slug is already taken.")
    try:
        actor = create_local_actor(
            slug, actor_type=actor_type, name=title, summary=description
        )
    except IntegrityError as exc:
        raise ValidationError("That handle is already taken.") from exc
    comic = Comic.objects.create(
        actor=actor,
        title=title,
        slug=slug,
        description=description,
        content_rating=content_rating,
        default_audience=default_audience,
        default_federation=default_federation,
        update_schedule=update_schedule or {},
    )
    ComicRole.objects.create(comic=comic, user=owner, role=ComicRole.Role.OWNER)
    if tags:
        comic.set_tags(tags)
    return comic


def _next_position(queryset):
    current = queryset.aggregate(Max("position"))["position__max"]
    return (current or 0) + 1


def create_series(user, comic, title, *, slug=None, description="", position=None):
    if not permissions.can_edit(user, comic):
        raise PermissionDenied("You cannot manage this comic's series.")
    series = Series(
        comic=comic,
        title=title,
        slug=slug or slugify(title),
        description=description,
        position=position if position is not None else _next_position(comic.series),
    )
    series.save()
    return series


def create_page(
    user,
    series,
    *,
    title="",
    chapter=None,
    audience=None,
    federation=None,
    **fields,
):
    comic = series.comic
    if not permissions.can_contribute(user, comic):
        raise PermissionDenied("You cannot contribute to this comic.")
    page = Page(
        series=series,
        chapter=chapter,
        title=title,
        position=fields.pop("position", _next_position(series.pages)),
        audience=audience or comic.default_audience,
        federation=federation or comic.default_federation,
        author=user,
        status=PageStatus.DRAFT,
        **fields,
    )
    page.save()
    return page
