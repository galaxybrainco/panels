from django.core.management.base import BaseCommand

from billing import lifecycle


class Command(BaseCommand):
    help = "Freeze plans past their grace window and archive old freezes."

    def handle(self, *args, **options):
        frozen = lifecycle.freeze_due_plans()
        archived = lifecycle.archive_old_plans()
        self.stdout.write(
            self.style.SUCCESS(f"Froze {frozen} plan(s), archived {archived}.")
        )
