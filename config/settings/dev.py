from .base import *  # noqa: F403

DEBUG = True
SECRET_KEY = SECRET_KEY or "insecure-dev-key"  # noqa: F405
ALLOWED_HOSTS = ["*"]
FIELD_ENCRYPTION_KEY = FIELD_ENCRYPTION_KEY or (  # noqa: F405
    "khtNoDFXLyvfkh2vVB8GRQmFfz_Q1ibc6XPEj8bTy8Y="
)
