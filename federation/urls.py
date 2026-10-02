from django.urls import path

from federation import views

urlpatterns = [
    path("inbox", views.shared_inbox, name="shared-inbox"),
    path("actors/<str:handle>/inbox", views.actor_inbox, name="actor-inbox"),
]
