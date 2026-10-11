IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


def build_media_storage(
    *,
    bucket="",
    endpoint_url="",
    access_key="",
    secret_key="",
    region="auto",
    custom_domain="",
):
    if not bucket:
        return {"BACKEND": "django.core.files.storage.FileSystemStorage"}
    options = {
        "bucket_name": bucket,
        "access_key": access_key,
        "secret_key": secret_key,
        "region_name": region,
        "addressing_style": "path",
        "querystring_auth": False,
        "file_overwrite": False,
        "object_parameters": {"CacheControl": IMMUTABLE_CACHE_CONTROL},
    }
    if endpoint_url:
        options["endpoint_url"] = endpoint_url
    if custom_domain:
        options["custom_domain"] = custom_domain
    return {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": options}
