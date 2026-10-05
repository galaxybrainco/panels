from django.contrib import admin
from django.urls import include, path

from accounts import views as account_views
from config.health import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/signup/",
        account_views.SignupView.as_view(),
        name="account_signup",
    ),
    path("accounts/", include("allauth.urls")),
    path("healthz", healthz, name="healthz"),
    path("", include("federation.urls")),
    path("", include("actors.urls")),
    path("", include("comics.urls")),
]
