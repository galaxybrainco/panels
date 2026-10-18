from django.core.management.base import BaseCommand

from memberships import bmac
from memberships.models import Provider, ProviderConnection


class Command(BaseCommand):
    help = "Reconcile supporter state against the Buy Me a Coffee API."

    def handle(self, *args, **options):
        reconciled = failed = 0
        for connection in ProviderConnection.objects.filter(provider=Provider.BMAC):
            try:
                bmac.reconcile_bmac(connection)
                reconciled += 1
            except bmac.BmacError as exc:
                failed += 1
                self.stderr.write(f"Failed {connection.comic_id}: {exc}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {reconciled} connection(s), {failed} failed."
            )
        )
