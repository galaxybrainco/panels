from django.apps import AppConfig


class ActorsConfig(AppConfig):
    name = "actors"

    def ready(self):
        from actors import handlers  # noqa: F401
