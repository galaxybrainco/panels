from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from comics.export import comic_payload, copy_media, write_payload
from comics.models import Comic


class Command(BaseCommand):
    help = "Export a comic's data as JSON, optionally copying media files."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--output", default="")
        parser.add_argument("--media-dir", default="")

    def handle(self, *args, **options):
        comic = (
            Comic.objects.filter(slug=options["slug"]).select_related("actor").first()
        )
        if comic is None:
            raise CommandError("No such comic")
        media_names = []
        data = {
            "exported_at": timezone.now().isoformat(),
            "comic": comic_payload(comic, media_names),
        }
        if options["media_dir"]:
            copy_media(media_names, options["media_dir"])
        payload = write_payload(data, options["output"])
        if payload is not None:
            self.stdout.write(payload)
