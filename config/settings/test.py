from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-secret-key"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
INSTANCE_URL = "http://testserver"
INSTANCE_DOMAIN = "testserver"
FIELD_ENCRYPTION_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
MAILERS = {
    "default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
}
ACCOUNT_RATE_LIMITS = False
