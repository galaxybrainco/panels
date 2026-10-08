from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404

from actors.activitypub import ACTIVITYPUB_CONTENT_TYPE
from comics.federation import federation_plan
from comics.models import PageStatus
from social.comments import comment_to_note
from social.models import Comment, CommentStatus


def comment_detail(request, comment_id):
    comment = get_object_or_404(Comment, pk=comment_id, status=CommentStatus.VISIBLE)
    if comment.ap_id != f"{settings.INSTANCE_URL}/comments/{comment.id}":
        raise Http404
    page = comment.page
    if page.status != PageStatus.PUBLISHED or not federation_plan(page).emit:
        raise Http404
    response = JsonResponse(
        comment_to_note(comment), content_type=ACTIVITYPUB_CONTENT_TYPE
    )
    response["Access-Control-Allow-Origin"] = "*"
    return response
