from django.conf import settings
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from comics.models import ComicRole


class OwnerRequiredError(Exception):
    pass


@receiver(pre_delete, sender=settings.AUTH_USER_MODEL)
def protect_comic_owners(sender, instance, **kwargs):
    if ComicRole.objects.filter(user=instance, role=ComicRole.Role.OWNER).exists():
        raise OwnerRequiredError(
            "Cannot delete a user who owns one or more comics; transfer ownership first."
        )
