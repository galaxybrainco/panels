from django.urls import path

from billing import views

urlpatterns = [
    path(
        "comics/<uuid:comic_id>/billing/checkout",
        views.checkout,
        name="billing-checkout",
    ),
]
