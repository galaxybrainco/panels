from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.utils import timezone

from comics import publishing
from comics.models import Page, PageStatus


class Command(BaseCommand):
    help = "Publish any scheduled pages whose scheduled_for time has passed."

    def handle(self, *args, **options):
        now = timezone.now()
        due = Page.objects.filter(
            status=PageStatus.SCHEDULED, scheduled_for__lte=now
        ).order_by("scheduled_for", "id")
        published = skipped = 0
        for page in due:
            try:
                publishing.publish(
                    page,
                    published_by=page.scheduled_by,
                    when=page.scheduled_for,
                    require_scheduled=True,
                )
                published += 1
            except ValidationError as exc:
                skipped += 1
                self.stderr.write(f"Skipped page {page.id}: {exc}")
        self.stdout.write(
            self.style.SUCCESS(f"Published {published} page(s), skipped {skipped}.")
        )
