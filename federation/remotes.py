import base64
from urllib.parse import urlparse

import base58
from django.utils import timezone

from actors.models import Actor, ActorType, Instance
from federation import client
from federation.keys import rsa_public_key_from_actor


def _split_key_id(key_id):
    return key_id.split("#", 1)[0]


def _ed25519_public_key(document):
    assertion = document.get("assertionMethod")
    if not isinstance(assertion, dict):
        return ""
    multibase = assertion.get("publicKeyMultibase")
    if not isinstance(multibase, str) or not multibase.startswith("z"):
        return ""
    raw = base58.b58decode(multibase[1:])
    if raw[:2] != b"\xed\x01":
        return ""
    return base64.b64encode(raw[2:]).decode("ascii")


def fetch_remote_actor(ap_id):
    document = client.fetch_json(ap_id)
    if not isinstance(document, dict):
        return None
    domain = urlparse(ap_id).netloc
    Instance.objects.get_or_create(domain=domain)
    public_key = document.get("publicKey")
    public_key_pem = (
        public_key.get("publicKeyPem", "") if isinstance(public_key, dict) else ""
    )
    endpoints = document.get("endpoints") or {}
    actor, _ = Actor.objects.update_or_create(
        ap_id=ap_id,
        defaults={
            "type": document.get("type") or ActorType.PERSON,
            "handle": document.get("preferredUsername", ""),
            "domain": domain,
            "name": document.get("name", ""),
            "summary": document.get("summary", ""),
            "inbox": document.get("inbox", ""),
            "shared_inbox": endpoints.get("sharedInbox", ""),
            "public_key_pem": public_key_pem,
            "ed25519_public_key": _ed25519_public_key(document),
            "last_fetched_at": timezone.now(),
        },
    )
    return actor


def resolve_actor_by_key_id(key_id):
    ap_id = _split_key_id(key_id)
    local = Actor.objects.filter(ap_id=ap_id).first()
    if local is not None:
        return local
    return fetch_remote_actor(ap_id)


def public_key_for_key_id(key_id):
    actor = resolve_actor_by_key_id(key_id)
    if actor is None or not actor.public_key_pem:
        return None
    return rsa_public_key_from_actor(actor)
