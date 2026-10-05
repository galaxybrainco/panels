from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.module_loading import import_string


class DerivativeKind(models.TextChoices):
    THUMBNAIL = "thumbnail", "Thumbnail"
    DISPLAY = "display", "Display"
    FEDERATION = "federation", "Federation"


DERIVATIVE_SPECS = {
    DerivativeKind.THUMBNAIL: {"width": 400, "quality": 80, "format": "webp"},
    DerivativeKind.DISPLAY: {"width": 1600, "quality": 82, "format": "webp"},
    DerivativeKind.FEDERATION: {"width": 4096, "quality": 85, "format": "jpeg"},
}


def _absolute_media_url(media) -> str:
    base = settings.INSTANCE_URL.rstrip("/")
    return f"{base}/{media.original.url.lstrip('/')}"


class DerivativeBackend:
    def url(self, media, kind) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class LocalDerivativeBackend(DerivativeBackend):
    """Development/self-host backend: serve the original, no transforms."""

    def url(self, media, kind) -> str:
        return _absolute_media_url(media)


class BunnyOptimizerBackend(DerivativeBackend):
    """Bunny Optimizer dynamic-images backend (query-param transforms)."""

    def __init__(self, base_url=None):
        base_url = base_url or getattr(settings, "MEDIA_CDN_BASE_URL", "")
        if not base_url:
            raise ImproperlyConfigured(
                "MEDIA_CDN_BASE_URL must be set for the Bunny optimizer backend."
            )
        self.base_url = base_url.rstrip("/")

    def url(self, media, kind) -> str:
        query = urlencode(DERIVATIVE_SPECS[kind])
        return f"{self.base_url}/{media.original.name.lstrip('/')}?{query}"


def get_derivative_backend() -> DerivativeBackend:
    path = getattr(
        settings,
        "MEDIA_DERIVATIVE_BACKEND",
        "media.derivative_urls.LocalDerivativeBackend",
    )
    return import_string(path)()


def derivative_url(media, kind) -> str:
    return get_derivative_backend().url(media, kind)
