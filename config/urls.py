from django.contrib import admin
from django.urls import include, path

from config.health import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("", include("actors.urls")),
]
