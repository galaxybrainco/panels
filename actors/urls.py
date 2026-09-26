from django.urls import path

from actors import views

urlpatterns = [
    path(".well-known/webfinger", views.webfinger, name="webfinger"),
    path(".well-known/nodeinfo", views.nodeinfo_discovery, name="nodeinfo-discovery"),
    path("nodeinfo/2.1", views.nodeinfo, name="nodeinfo"),
    path("FEDERATION.md", views.federation_md, name="federation-md"),
    path("actors/<str:handle>", views.actor_detail, name="actor-detail"),
]
