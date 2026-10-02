import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.text import slugify

from federation.activitypub import Audience


class ContentRating(models.TextChoices):
    ALL_AGES = "all_ages", "All ages"
    TEEN = "teen", "Teen"


class FederationMode(models.TextChoices):
    FEDERATED = "federated", "Federated"
    LOCAL_ONLY = "local_only", "Local only"


class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Comic(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.OneToOneField(
        "actors.Actor", on_delete=models.PROTECT, related_name="comic"
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True, default="")
    content_rating = models.CharField(
        max_length=16, choices=ContentRating.choices, default=ContentRating.ALL_AGES
    )
    update_schedule = models.JSONField(default=dict, blank=True)
    default_audience = models.CharField(
        max_length=16, choices=Audience.choices, default=Audience.PUBLIC
    )
    default_federation = models.CharField(
        max_length=16,
        choices=FederationMode.choices,
        default=FederationMode.FEDERATED,
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="comics")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def set_tags(self, names):
        tags = []
        for name in names:
            tag, _ = Tag.objects.get_or_create(
                name=name, defaults={"slug": slugify(name)}
            )
            tags.append(tag)
        self.tags.set(tags)


class ComicRole(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        EDITOR = "editor", "Editor"
        CONTRIBUTOR = "contributor", "Contributor"
        MODERATOR = "moderator", "Moderator"

    comic = models.ForeignKey(Comic, on_delete=models.CASCADE, related_name="roles")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comic_roles"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "user"], name="unique_user_role_per_comic"
            ),
            models.UniqueConstraint(
                fields=["comic"],
                condition=Q(role="owner"),
                name="one_owner_per_comic",
            ),
        ]

    def __str__(self):
        return f"{self.user} is {self.role} of {self.comic}"
