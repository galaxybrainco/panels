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
    def __init__(self, name="media/abc/original.png", url="media/abc/original.png"):
        self.name = name
        self.url = url


class _StubMedia:
    def __init__(
        self, *, width=1000, height=800, name=None, url=None, content_type="image/png"
    ):
        self.width = width
        self.height = height
        self.content_type = content_type
        self.original = _StubOriginal(
            name=name or "media/abc/original.png",
            url=url or "media/abc/original.png",
        )


@pytest.fixture
def media():
    return _StubMedia()


def test_local_backend_returns_absolute_storage_url(media):
    url = LocalDerivativeBackend().url(media, DerivativeKind.THUMBNAIL)
    assert url == "http://testserver/media/abc/original.png"


def test_local_backend_preserves_absolute_storage_url():
    media = _StubMedia(url="https://bucket.test/media/abc/original.png")
    url = LocalDerivativeBackend().url(media, DerivativeKind.THUMBNAIL)
    assert url == "https://bucket.test/media/abc/original.png"


def test_bunny_backend_preserves_path_and_adds_transform_params(media):
    url = BunnyOptimizerBackend("https://cdn.test").url(media, DerivativeKind.DISPLAY)
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://cdn.test/media/abc/original.png"
    )
    query = parse_qs(parsed.query)
    assert query == {"width": ["1600"], "quality": ["82"], "format": ["webp"]}


def test_bunny_federation_caps_longest_side_for_landscape():
    media = _StubMedia(width=8000, height=2000)
    query = parse_qs(
        urlparse(
            BunnyOptimizerBackend("https://cdn.test").url(
                media, DerivativeKind.FEDERATION
            )
        ).query
    )
    assert query == {"width": ["4096"], "quality": ["85"], "format": ["jpeg"]}


def test_bunny_federation_caps_longest_side_for_portrait():
    media = _StubMedia(width=2000, height=8000)
    query = parse_qs(
        urlparse(
            BunnyOptimizerBackend("https://cdn.test").url(
                media, DerivativeKind.FEDERATION
            )
        ).query
    )
    assert query == {"height": ["4096"], "quality": ["85"], "format": ["jpeg"]}


def test_bunny_backend_requires_base_url():
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


def test_local_backend_content_type_is_the_original(media):
    assert (
        LocalDerivativeBackend().content_type(media, DerivativeKind.FEDERATION)
        == "image/png"
    )


def test_bunny_backend_content_type_is_the_derivative_format(media):
    assert (
        BunnyOptimizerBackend("https://cdn.test").content_type(
            media, DerivativeKind.FEDERATION
        )
        == "image/jpeg"
    )
