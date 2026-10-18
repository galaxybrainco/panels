from django.urls import path

from memberships import views

urlpatterns = [
    path(
        "webhooks/kofi/<uuid:comic_id>",
        views.kofi_webhook,
        name="kofi-webhook",
    ),
    path(
        "webhooks/bmac/<uuid:comic_id>/<str:secret>",
        views.bmac_webhook,
        name="bmac-webhook",
    ),
]
