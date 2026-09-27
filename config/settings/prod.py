from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False

SECRET_KEY_PLACEHOLDER = "dev-only-change-me"
MIN_SECRET_KEY_LENGTH = 50

if (
    not SECRET_KEY  # noqa: F405
    or SECRET_KEY == SECRET_KEY_PLACEHOLDER  # noqa: F405
    or len(SECRET_KEY) < MIN_SECRET_KEY_LENGTH  # noqa: F405
):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a strong, non-placeholder value of at "
        f"least {MIN_SECRET_KEY_LENGTH} characters in production"
    )

if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production")

if not INSTANCE_URL.startswith("https://"):  # noqa: F405
    raise ImproperlyConfigured("INSTANCE_URL must use https in production")

if not FIELD_ENCRYPTION_KEY:  # noqa: F405
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY must be set in production")
try:
    Fernet(FIELD_ENCRYPTION_KEY.encode())  # noqa: F405
except (ValueError, TypeError) as exc:
    raise ImproperlyConfigured(
        "FIELD_ENCRYPTION_KEY is not a valid Fernet key"
    ) from exc

if env("AWS_STORAGE_BUCKET_NAME", default=""):  # noqa: F405
    STORAGES["default"] = {  # noqa: F405
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("AWS_STORAGE_BUCKET_NAME"),  # noqa: F405
            "endpoint_url": env("AWS_S3_ENDPOINT_URL", default=None),  # noqa: F405
            "access_key": env("AWS_ACCESS_KEY_ID", default=None),  # noqa: F405
            "secret_key": env("AWS_SECRET_ACCESS_KEY", default=None),  # noqa: F405
            "custom_domain": env("AWS_S3_CUSTOM_DOMAIN", default=None),  # noqa: F405
            "querystring_auth": False,
            "file_overwrite": False,
        },
    }
