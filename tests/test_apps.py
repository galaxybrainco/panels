import pytest
from django.apps import apps

SPEC_APPS = [
    "accounts",
    "actors",
    "comics",
    "media",
    "federation",
    "social",
    "memberships",
    "moderation",
    "feeds",
]


@pytest.mark.parametrize("label", SPEC_APPS)
def test_spec_app_is_installed(label):
    assert apps.is_installed(label)
