from django.core.management.base import BaseCommand
from django.utils import timezone

from memberships.models import ProviderSupporter, SupporterStatus


class Command(BaseCommand):
    help = "Deactivate lapsed provider supporters and supporter statuses."

    def handle(self, *args, **options):
        now = timezone.now()
        supporters = ProviderSupporter.objects.filter(
            active=True, valid_until__lt=now
        ).update(active=False)
        statuses = SupporterStatus.objects.filter(
            active=True, valid_until__lt=now
        ).update(active=False)
        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {supporters} supporter(s), {statuses} status(es)."
            )
        )
