"""Media storage backend selection.

Dev/test use the local filesystem. Production uses an S3-compatible backend
(Bunny Storage). Immutable caching is expected to be applied at the CDN
(pull-zone) layer via the ``IMMUTABLE_CACHE_CONTROL`` value below, because
Bunny's S3 API does not support an object-level ``Cache-Control`` header.
"""

IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


def build_media_storage(
    *,
    bucket="",
    endpoint_url="",
    access_key="",
    secret_key="",
    region="de",
    custom_domain="",
):
    if not bucket:
        return {"BACKEND": "django.core.files.storage.FileSystemStorage"}
    if not endpoint_url:
        raise ValueError(
            "MEDIA_STORAGE_ENDPOINT_URL is required when a storage bucket is set."
        )
    options = {
        "bucket_name": bucket,
        "access_key": access_key,
        "secret_key": secret_key,
        "region_name": region,
        "endpoint_url": endpoint_url,
        "addressing_style": "path",
        "querystring_auth": False,
        "file_overwrite": False,
    }
    if custom_domain:
        options["custom_domain"] = custom_domain
    return {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": options}
