import pytest
from django.core.management import call_command

from actors.models import Actor


@pytest.mark.django_db
def test_create_instance_actor_is_idempotent(capsys):
    call_command("create_instance_actor")
    call_command("create_instance_actor")

    actors = Actor.objects.filter(is_instance_actor=True)
    assert actors.count() == 1
    actor = actors.get()
    assert actor.is_local
    assert actor.ap_id.endswith("/actors/instance")
    assert actor.public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")
