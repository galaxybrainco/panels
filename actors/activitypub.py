import base64

from actors.models import Actor
from federation.keys import ed25519_multikey

ACTIVITYPUB_CONTENT_TYPE = "application/activity+json"


def actor_to_activitypub(actor: Actor) -> dict:
    return {
        "@context": [
            "https://www.w3.org/ns/activitystreams",
            "https://w3id.org/security/v1",
            "https://w3id.org/security/multikey/v1",
        ],
        "id": actor.ap_id,
        "type": actor.type,
        "preferredUsername": actor.handle,
        "name": actor.name,
        "summary": actor.summary,
        "inbox": actor.inbox,
        "outbox": actor.outbox,
        "followers": actor.followers,
        "following": actor.following,
        "featured": actor.featured,
        "manuallyApprovesFollowers": actor.manually_approves_followers,
        "discoverable": actor.discoverable,
        "indexable": actor.indexable,
        "publicKey": {
            "id": f"{actor.ap_id}#main-key",
            "owner": actor.ap_id,
            "publicKeyPem": actor.public_key_pem,
        },
        "assertionMethod": {
            "id": f"{actor.ap_id}#ed25519-key",
            "type": "Multikey",
            "controller": actor.ap_id,
            "publicKeyMultibase": ed25519_multikey(
                base64.b64decode(actor.ed25519_public_key)
            ),
        },
        "endpoints": {"sharedInbox": actor.shared_inbox},
    }
