from django.apps import AppConfig


class ModerationConfig(AppConfig):
    name = "moderation"

    def ready(self):
        from moderation import handlers  # noqa: F401
