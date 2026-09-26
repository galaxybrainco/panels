from .base import *  # noqa: F403

DEBUG = True
SECRET_KEY = SECRET_KEY or "insecure-dev-key"  # noqa: F405
ALLOWED_HOSTS = ["*"]
