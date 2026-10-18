import logging

from django.tasks import task

from memberships import bmac
from memberships.models import ProviderConnection

logger = logging.getLogger(__name__)


@task
def reconcile_bmac_comic(connection_id):
    connection = ProviderConnection.objects.filter(pk=connection_id).first()
    if connection is None:
        return
    try:
        bmac.reconcile_bmac(connection)
    except bmac.BmacError as exc:
        logger.warning("BMAC reconcile failed for %s: %s", connection_id, exc)
