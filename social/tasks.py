from django.tasks import task

from social import crawl
from social.models import Comment


@task
def crawl_comment_thread(comment_id):
    comment = Comment.objects.filter(pk=comment_id).first()
    if comment is None:
        return
    crawl.crawl_comment(comment)
