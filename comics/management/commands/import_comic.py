from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from comics import permissions
from comics.importers import import_directory, import_feed
from comics.models import Comic, ContentRating
from comics.services import create_comic, create_series


class Command(BaseCommand):
    help = "Import a comic from an image directory or an RSS/Atom feed."

    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True, help="Owner email")
        parser.add_argument("--comic", default="", help="Existing comic slug")
        parser.add_argument("--handle", default="")
        parser.add_argument("--title", default="")
        parser.add_argument("--series", default="Imported")
        parser.add_argument("--tags", default="")
        parser.add_argument(
            "--rating",
            choices=[choice.value for choice in ContentRating],
            default=ContentRating.ALL_AGES,
        )
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--directory")
        source.add_argument("--feed")

    def handle(self, *args, **options):
        owner = get_user_model().objects.filter(email=options["owner"]).first()
        if owner is None:
            raise CommandError("No such owner")
        if options["comic"]:
            comic = Comic.objects.filter(slug=options["comic"]).first()
            if comic is None:
                raise CommandError("No such comic")
            if not permissions.can_edit(owner, comic):
                raise CommandError("Owner cannot edit that comic")
        else:
            if not options["handle"] or not options["title"]:
                raise CommandError("--handle and --title are required for a new comic")
            tags = [item.strip() for item in options["tags"].split(",") if item.strip()]
            comic = create_comic(
                owner,
                options["handle"],
                options["title"],
                content_rating=options["rating"],
                tags=tags,
            )
        series_slug = slugify(options["series"]) or "imported"
        series = comic.series.filter(slug=series_slug).first()
        if series is None:
            series = create_series(owner, comic, options["series"], slug=series_slug)
        if options["directory"]:
            result = import_directory(owner, comic, series, options["directory"])
        else:
            result = import_feed(owner, comic, series, options["feed"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {result['pages']} page(s), {result['media']} media, "
                f"skipped {result['skipped']}."
            )
        )
