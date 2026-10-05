from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.text import slugify

from core.models import UUIDModel
from federation.activitypub import Audience


class ContentRating(models.TextChoices):
    ALL_AGES = "all_ages", "All ages"
    TEEN = "teen", "Teen"


class FederationMode(models.TextChoices):
    FEDERATED = "federated", "Federated"
    LOCAL_ONLY = "local_only", "Local only"


class Tag(UUIDModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Comic(UUIDModel):
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

    class Meta:
        ordering = ["title", "id"]

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


class Series(UUIDModel):
    comic = models.ForeignKey(Comic, on_delete=models.CASCADE, related_name="series")
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True, default="")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "slug"], name="unique_series_slug_per_comic"
            ),
            models.UniqueConstraint(
                fields=["comic", "position"], name="unique_series_position"
            ),
        ]
        ordering = ["comic", "position", "id"]
        verbose_name_plural = "series"

    def __str__(self):
        return self.title


class Chapter(UUIDModel):
    series = models.ForeignKey(
        Series, on_delete=models.CASCADE, related_name="chapters"
    )
    title = models.CharField(max_length=255)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["series", "position"], name="unique_chapter_position"
            )
        ]
        ordering = ["series", "position"]

    def __str__(self):
        return self.title


class PageStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SCHEDULED = "scheduled", "Scheduled"
    PUBLISHED = "published", "Published"


class Page(UUIDModel):
    series = models.ForeignKey(Series, on_delete=models.CASCADE, related_name="pages")
    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pages",
    )
    position = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255, blank=True, default="")
    transcript = models.TextField(blank=True, default="")
    author_commentary = models.TextField(blank=True, default="")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authored_pages",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_pages",
    )
    scheduled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_pages",
    )
    status = models.CharField(
        max_length=16, choices=PageStatus.choices, default=PageStatus.DRAFT
    )
    audience = models.CharField(
        max_length=16, choices=Audience.choices, default=Audience.PUBLIC
    )
    federation = models.CharField(
        max_length=16,
        choices=FederationMode.choices,
        default=FederationMode.FEDERATED,
    )
    content_warning = models.CharField(max_length=255, blank=True, default="")
    sensitive = models.BooleanField(default=False)
    ap_id = models.URLField(blank=True, default="")
    scheduled_for = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["series", "position"], name="unique_page_position"
            )
        ]
        indexes = [
            models.Index(fields=["status", "scheduled_for"], name="page_due_idx")
        ]
        ordering = ["series", "position", "id"]

    def __str__(self):
        return f"{self.series} #{self.position}"
