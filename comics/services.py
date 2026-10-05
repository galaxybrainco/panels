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

PAGE_OVERRIDE_FIELDS = {
    "transcript",
    "author_commentary",
    "content_warning",
    "sensitive",
}

UPDATE_FIELDS = {
    "title",
    "transcript",
    "author_commentary",
    "content_warning",
    "sensitive",
    "audience",
    "federation",
}


def _validate_choice(value, choices, field):
    if value not in choices.values:
        raise ValidationError({field: f"Invalid value: {value!r}."})
    return value


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
    _validate_choice(content_rating, ContentRating, "content_rating")
    _validate_choice(default_audience, Audience, "default_audience")
    _validate_choice(default_federation, FederationMode, "default_federation")
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


@transaction.atomic
def create_series(user, comic, title, *, slug=None, description="", position=None):
    if not permissions.can_edit(user, comic):
        raise PermissionDenied("You cannot manage this comic's series.")
    comic = Comic.objects.select_for_update().get(pk=comic.pk)
    series_slug = slug or slugify(title)
    if not series_slug:
        raise ValidationError("A series slug is required.")
    if Series.objects.filter(comic=comic, slug=series_slug).exists():
        raise ValidationError("That series slug is already taken.")
    series = Series(
        comic=comic,
        title=title,
        slug=series_slug,
        description=description,
        position=position if position is not None else _next_position(comic.series),
    )
    series.save()
    return series


@transaction.atomic
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
    if not permissions.can_author(user, comic):
        raise PermissionDenied("You cannot contribute to this comic.")
    unknown = set(fields) - PAGE_OVERRIDE_FIELDS
    if unknown:
        raise TypeError(f"Unsupported page fields: {sorted(unknown)}")
    if chapter is not None and chapter.series_id != series.id:
        raise ValidationError("Chapter does not belong to this series.")
    audience = _validate_choice(
        audience or comic.default_audience, Audience, "audience"
    )
    federation = _validate_choice(
        federation or comic.default_federation, FederationMode, "federation"
    )
    series = Series.objects.select_for_update().get(pk=series.pk)
    page = Page(
        series=series,
        chapter=chapter,
        title=title,
        position=_next_position(series.pages),
        audience=audience,
        federation=federation,
        author=user,
        status=PageStatus.DRAFT,
        **fields,
    )
    page.save()
    return page


@transaction.atomic
def update_page(user, page, **fields):
    comic = page.series.comic
    if not permissions.can_author(user, comic):
        raise PermissionDenied("You cannot edit this page.")
    unknown = set(fields) - UPDATE_FIELDS
    if unknown:
        raise TypeError(f"Unsupported page fields: {sorted(unknown)}")
    locked = Page.objects.select_for_update().get(pk=page.pk)
    if locked.status == PageStatus.PUBLISHED and not permissions.can_publish(
        user, comic
    ):
        raise PermissionDenied("You cannot edit a published page.")
    if "audience" in fields:
        _validate_choice(fields["audience"], Audience, "audience")
    if "federation" in fields:
        _validate_choice(fields["federation"], FederationMode, "federation")

    from comics import federation

    was_plan = federation.federation_plan(locked)
    for name, value in fields.items():
        setattr(locked, name, value)
    locked.save()
    if locked.status == PageStatus.PUBLISHED:
        plan = federation.federation_plan(locked)
        if plan.emit and not was_plan.emit:
            federation.emit_page_activity(locked, "Create")
        elif plan.emit:
            federation.emit_page_activity(locked, "Update")
        elif was_plan.emit:
            federation.emit_page_activity(locked, "Delete", plan=was_plan)
    return locked
