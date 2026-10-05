from django.urls import path

from comics import views

urlpatterns = [
    path("pages/<uuid:page_id>", views.page_detail, name="page-detail"),
]
