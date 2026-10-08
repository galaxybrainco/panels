from django.urls import path

from social import views

urlpatterns = [
    path("comments/<uuid:comment_id>", views.comment_detail, name="comment-detail"),
]
