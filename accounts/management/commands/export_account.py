import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from actors.models import Actor
from comics.export import comic_payload, copy_media
from comics.models import Comic
from social.models import Comment, Follow, ReadingProgress


class Command(BaseCommand):
    help = "Export a user's account data as JSON, optionally copying media files."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--output", default="")
        parser.add_argument("--media-dir", default="")

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(email=options["email"]).first()
        if user is None:
            raise CommandError("No such user")
        actor = Actor.objects.filter(user=user).first()
        media_names = []
        data = {
            "exported_at": timezone.now().isoformat(),
            "user": {
                "id": str(user.id),
                "email": user.email,
                "date_joined": user.date_joined.isoformat(),
            },
            "actor": self._actor(actor),
            "followers": list(
                Follow.objects.filter(target=actor).values_list(
                    "follower__ap_id", flat=True
                )
            )
            if actor
            else [],
            "following": list(
                Follow.objects.filter(follower=actor).values_list(
                    "target__ap_id", flat=True
                )
            )
            if actor
            else [],
            "comics": [],
            "comments": list(
                Comment.objects.filter(actor__user=user).values(
                    "ap_id", "content", "created_at", "page__ap_id"
                )
            )
            if actor
            else [],
            "reading_progress": list(
                ReadingProgress.objects.filter(user=user).values(
                    "series__comic__slug", "series__slug", "page__position"
                )
            ),
        }
        comics = (
            Comic.objects.filter(roles__user=user, roles__role="owner")
            .distinct()
            .order_by("slug")
        )
        for comic in comics:
            data["comics"].append(comic_payload(comic, media_names))
        if options["media_dir"]:
            copy_media(media_names, options["media_dir"])
        payload = json.dumps(data, indent=2, default=str)
        if options["output"]:
            Path(options["output"]).write_text(payload, encoding="utf-8")
        else:
            self.stdout.write(payload)

    def _actor(self, actor):
        if actor is None:
            return None
        return {
            "ap_id": actor.ap_id,
            "handle": actor.handle,
            "name": actor.name,
            "summary": actor.summary,
            "also_known_as": actor.also_known_as,
            "moved_to": actor.moved_to,
        }
