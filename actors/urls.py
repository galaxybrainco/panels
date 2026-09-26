from django.urls import path

from actors import views

urlpatterns = [
    path(".well-known/webfinger", views.webfinger, name="webfinger"),
    path("actors/<str:handle>", views.actor_detail, name="actor-detail"),
]
