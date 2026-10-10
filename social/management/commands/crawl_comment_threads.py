from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from comics.models import PageStatus
from social.models import Comment
from social.tasks import crawl_comment_thread

CRAWL_TTL = timedelta(hours=24)
MAX_SWEEP = 100


class Command(BaseCommand):
    help = "Enqueue crawls for comment threads that are stale."

    def handle(self, *args, **options):
        cutoff = timezone.now() - CRAWL_TTL
        stale = (
            Comment.objects.filter(page__status=PageStatus.PUBLISHED)
            .filter(Q(crawled_at__isnull=True) | Q(crawled_at__lt=cutoff))
            .order_by("crawled_at")[:MAX_SWEEP]
        )
        enqueued = 0
        for comment in stale:
            crawl_comment_thread.enqueue(str(comment.id))
            enqueued += 1
        self.stdout.write(self.style.SUCCESS(f"Enqueued {enqueued} crawl(s)."))
