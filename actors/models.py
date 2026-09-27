import uuid

from django.db import models


class ActorType(models.TextChoices):
    PERSON = "Person", "Person"
    SERVICE = "Service", "Service"
    GROUP = "Group", "Group"
    ORGANIZATION = "Organization", "Organization"


class Actor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ap_id = models.URLField(unique=True)
    type = models.CharField(
        max_length=32, choices=ActorType.choices, default=ActorType.PERSON
    )
    handle = models.CharField(max_length=255)
    domain = models.CharField(max_length=255, blank=True, default="")
    name = models.CharField(max_length=255, blank=True, default="")
    summary = models.TextField(blank=True, default="")

    inbox = models.URLField(blank=True, default="")
    shared_inbox = models.URLField(blank=True, default="")
    outbox = models.URLField(blank=True, default="")
    followers = models.URLField(blank=True, default="")
    following = models.URLField(blank=True, default="")
    featured = models.URLField(blank=True, default="")

    public_key_pem = models.TextField(blank=True, default="")
    ed25519_public_key = models.CharField(max_length=255, blank=True, default="")
    private_key_pem = models.BinaryField(blank=True, default=b"")
    ed25519_private_key = models.BinaryField(blank=True, default=b"")

    manually_approves_followers = models.BooleanField(default=False)
    indexable = models.BooleanField(default=True)
    discoverable = models.BooleanField(default=True)
    is_instance_actor = models.BooleanField(default=False)

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actor",
    )
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["handle", "domain"],
                name="unique_actor_handle_per_domain",
            ),
        ]

    def __str__(self):
        return self.ap_id

    @property
    def is_local(self):
        return not self.domain


class Instance(models.Model):
    domain = models.CharField(max_length=255, unique=True)
    software_name = models.CharField(max_length=255, blank=True, default="")
    software_version = models.CharField(max_length=255, blank=True, default="")
    shared_inbox = models.URLField(blank=True, default="")

    blocked = models.BooleanField(default=False)
    silenced = models.BooleanField(default=False)
    allowlisted = models.BooleanField(default=False)
    reject_media = models.BooleanField(default=False)
    reject_reports = models.BooleanField(default=False)

    last_fetched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.domain
