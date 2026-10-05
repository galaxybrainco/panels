from urllib.parse import parse_qs, urlparse

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from media.derivative_urls import (
    BunnyOptimizerBackend,
    DerivativeKind,
    LocalDerivativeBackend,
    derivative_url,
    get_derivative_backend,
)


class _StubOriginal:
    name = "media/abc/original.png"
    url = "media/abc/original.png"


class _StubMedia:
    original = _StubOriginal()


@pytest.fixture
def media():
    return _StubMedia()


def test_local_backend_returns_absolute_storage_url(media):
    url = LocalDerivativeBackend().url(media, DerivativeKind.THUMBNAIL)
    assert url == "http://testserver/media/abc/original.png"


def test_bunny_backend_preserves_path_and_adds_transform_params(media):
    url = BunnyOptimizerBackend("https://cdn.test").url(media, DerivativeKind.DISPLAY)
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://cdn.test/media/abc/original.png"
    )
    query = parse_qs(parsed.query)
    assert query == {"width": ["1600"], "quality": ["82"], "format": ["webp"]}


def test_bunny_federation_variant_is_jpeg(media):
    query = parse_qs(
        urlparse(
            BunnyOptimizerBackend("https://cdn.test").url(
                media, DerivativeKind.FEDERATION
            )
        ).query
    )
    assert query["format"] == ["jpeg"]
    assert query["width"] == ["4096"]


def test_bunny_backend_requires_base_url(media):
    with override_settings(MEDIA_CDN_BASE_URL=""):
        with pytest.raises(ImproperlyConfigured):
            BunnyOptimizerBackend()


@override_settings(
    MEDIA_DERIVATIVE_BACKEND="media.derivative_urls.BunnyOptimizerBackend",
    MEDIA_CDN_BASE_URL="https://cdn.test",
)
def test_backend_is_config_driven(media):
    assert isinstance(get_derivative_backend(), BunnyOptimizerBackend)
    assert derivative_url(media, DerivativeKind.THUMBNAIL).startswith(
        "https://cdn.test/media/abc/original.png?"
    )


def test_default_backend_is_local(media):
    assert isinstance(get_derivative_backend(), LocalDerivativeBackend)
    assert derivative_url(media, DerivativeKind.THUMBNAIL).startswith(
        "http://testserver/"
    )
