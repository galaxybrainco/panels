from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from actors import crypto
from actors.models import Actor, ActorType
from actors.services import create_local_actor


class Command(BaseCommand):
    help = "Create the instance actor if it does not already exist."

    def handle(self, *args, **options):
        existing = Actor.objects.filter(is_instance_actor=True, domain="").first()
        if existing is not None:
            self.stdout.write(f"Instance actor already exists: {existing.ap_id}")
            return
        try:
            actor = create_local_actor(
                settings.INSTANCE_ACTOR_HANDLE,
                actor_type=ActorType.SERVICE,
                name=settings.INSTANCE_NAME,
                summary=settings.INSTANCE_DESCRIPTION,
                is_instance_actor=True,
            )
        except crypto.KeyEncryptionError as exc:
            raise CommandError(f"Cannot create the instance actor: {exc}") from exc
        self.stdout.write(f"Created instance actor: {actor.ap_id}")
