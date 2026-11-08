import json
from pathlib import Path

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from actors.models import Actor
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
            data["comics"].append(self._comic(comic, media_names))
        if options["media_dir"]:
            self._copy_media(media_names, options["media_dir"])
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

    def _comic(self, comic, media_names):
        series_data = []
        for series in comic.series.order_by("position", "id"):
            pages = []
            for page in series.pages.order_by("position", "id"):
                media = []
                for item in page.media.order_by("position", "id"):
                    media_names.append(item.original.name)
                    media.append(
                        {
                            "id": str(item.id),
                            "file": item.original.name,
                            "alt_text": item.alt_text,
                            "content_type": item.content_type,
                            "width": item.width,
                            "height": item.height,
                            "bytes": item.bytes,
                            "sha256": item.sha256,
                        }
                    )
                pages.append(
                    {
                        "id": str(page.id),
                        "position": page.position,
                        "title": page.title,
                        "status": page.status,
                        "published_at": page.published_at,
                        "ap_id": page.ap_id,
                        "transcript": page.transcript,
                        "author_commentary": page.author_commentary,
                        "sensitive": page.sensitive,
                        "media": media,
                    }
                )
            series_data.append(
                {"title": series.title, "slug": series.slug, "pages": pages}
            )
        return {
            "slug": comic.slug,
            "title": comic.title,
            "description": comic.description,
            "content_rating": comic.content_rating,
            "payment_links": comic.payment_links,
            "series": series_data,
        }

    def _copy_media(self, names, media_dir):
        root = Path(media_dir)
        for name in names:
            target = (root / name).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError as exc:
                raise CommandError(f"Unsafe media path: {name}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with default_storage.open(name, "rb") as source:
                target.write_bytes(source.read())
