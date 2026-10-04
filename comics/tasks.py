from django.tasks import task
from django.utils import timezone

from comics import publishing
from comics.models import Page, PageStatus


@task
def publish_scheduled_page(page_id):
    page = Page.objects.filter(pk=page_id).first()
    if page is None or page.status != PageStatus.SCHEDULED:
        return
    if page.scheduled_for and page.scheduled_for > timezone.now():
        publish_scheduled_page.using(run_after=page.scheduled_for).enqueue(page_id)
        return
    publishing.publish(page, published_by=page.scheduled_by, when=page.scheduled_for)
