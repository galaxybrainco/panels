from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from actors.handles import validate_handle
from actors.models import ActorType
from actors.services import create_local_actor
from comics.models import Comic, ComicRole, ContentRating, FederationMode
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
