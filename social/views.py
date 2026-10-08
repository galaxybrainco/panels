from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from actors.activitypub import ACTIVITYPUB_CONTENT_TYPE
from social.comments import comment_to_note
from social.models import Comment, CommentStatus


def comment_detail(request, comment_id):
    comment = get_object_or_404(
        Comment,
        pk=comment_id,
        status=CommentStatus.VISIBLE,
        ap_id__startswith=settings.INSTANCE_URL,
    )
    response = JsonResponse(
        comment_to_note(comment), content_type=ACTIVITYPUB_CONTENT_TYPE
    )
    response["Access-Control-Allow-Origin"] = "*"
    return response
