import pytest

from media.storage import IMMUTABLE_CACHE_CONTROL, build_media_storage


def test_no_bucket_keeps_local_filesystem():
    config = build_media_storage(bucket="")
    assert config == {"BACKEND": "django.core.files.storage.FileSystemStorage"}


def test_bucket_without_endpoint_is_rejected():
    with pytest.raises(ValueError):
        build_media_storage(bucket="panels")


def test_bucket_configures_s3():
    config = build_media_storage(
        bucket="panels",
        endpoint_url="https://de-s3.storage.bunnycdn.com",
        access_key="key",
        secret_key="secret",
        region="de",
        custom_domain="cdn.example.test",
    )
    assert config["BACKEND"] == "storages.backends.s3.S3Storage"
    options = config["OPTIONS"]
    assert options["bucket_name"] == "panels"
    assert options["endpoint_url"] == "https://de-s3.storage.bunnycdn.com"
    assert options["custom_domain"] == "cdn.example.test"
    assert options["addressing_style"] == "path"
    assert options["querystring_auth"] is False
    assert options["region_name"] == "de"


def test_immutable_cache_control_is_a_year():
    assert IMMUTABLE_CACHE_CONTROL.startswith("public, max-age=")
    assert "immutable" in IMMUTABLE_CACHE_CONTROL
