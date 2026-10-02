from django.urls import path

from federation import views

urlpatterns = [
    path("inbox", views.shared_inbox, name="shared-inbox"),
    path("actors/<str:handle>/inbox", views.actor_inbox, name="actor-inbox"),
    path("actors/<str:handle>/outbox", views.actor_outbox, name="actor-outbox"),
    path(
        "actors/<str:handle>/followers", views.actor_followers, name="actor-followers"
    ),
    path(
        "actors/<str:handle>/following", views.actor_following, name="actor-following"
    ),
    path("actors/<str:handle>/featured", views.actor_featured, name="actor-featured"),
]
