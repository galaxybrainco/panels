from media.storage import IMMUTABLE_CACHE_CONTROL, build_media_storage


def test_no_bucket_keeps_local_filesystem():
    config = build_media_storage(bucket="")
    assert config == {"BACKEND": "django.core.files.storage.FileSystemStorage"}


def test_bucket_configures_s3_with_immutable_cache():
    config = build_media_storage(
        bucket="panels",
        endpoint_url="https://ny.storage.bunnycdn.com",
        access_key="key",
        secret_key="secret",
        region="ny",
        custom_domain="cdn.example.test",
    )
    assert config["BACKEND"] == "storages.backends.s3.S3Storage"
    options = config["OPTIONS"]
    assert options["bucket_name"] == "panels"
    assert options["endpoint_url"] == "https://ny.storage.bunnycdn.com"
    assert options["custom_domain"] == "cdn.example.test"
    assert options["addressing_style"] == "path"
    assert options["querystring_auth"] is False
    assert options["object_parameters"] == {"CacheControl": IMMUTABLE_CACHE_CONTROL}
