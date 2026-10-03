from django.apps import AppConfig


class ComicsConfig(AppConfig):
    name = "comics"

    def ready(self):
        from comics import signals  # noqa: F401
