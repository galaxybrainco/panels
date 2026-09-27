import base64

from django.conf import settings
from django.db import transaction

from actors import crypto
from actors.models import Actor, ActorType


def build_local_actor_urls(handle: str) -> dict[str, str]:
    base = settings.INSTANCE_URL.rstrip("/")
    actor_url = f"{base}/actors/{handle}"
    return {
        "ap_id": actor_url,
        "inbox": f"{actor_url}/inbox",
        "outbox": f"{actor_url}/outbox",
        "followers": f"{actor_url}/followers",
        "following": f"{actor_url}/following",
        "featured": f"{actor_url}/featured",
        "shared_inbox": f"{base}/inbox",
    }


@transaction.atomic
def create_local_actor(
    handle,
    *,
    actor_type=ActorType.PERSON,
    name="",
    summary="",
    user=None,
    is_instance_actor=False,
    manually_approves_followers=False,
):
    actor = Actor(
        type=actor_type,
        handle=handle,
        domain="",
        name=name,
        summary=summary,
        user=user,
        is_instance_actor=is_instance_actor,
        manually_approves_followers=manually_approves_followers,
        **build_local_actor_urls(handle),
    )
    rsa_private, rsa_public = crypto.generate_rsa_keypair()
    ed_private, ed_public = crypto.generate_ed25519_keypair()
    actor.public_key_pem = rsa_public.decode()
    actor.ed25519_public_key = base64.b64encode(ed_public).decode()
    actor.private_key_pem = crypto.encrypt(rsa_private)
    actor.ed25519_private_key = crypto.encrypt(ed_private)
    actor.save()
    return actor
