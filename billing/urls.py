from django.urls import path

from billing import views

urlpatterns = [
    path("billing/webhook", views.webhook, name="billing-webhook"),
    path(
        "comics/<uuid:comic_id>/billing/checkout",
        views.checkout,
        name="billing-checkout",
    ),
    path(
        "comics/<uuid:comic_id>/billing/portal",
        views.portal,
        name="billing-portal",
    ),
]
