import base64

from actors.models import Actor
from federation.keys import ed25519_multikey

ACTIVITYPUB_CONTENT_TYPE = "application/activity+json"


def _payment_attachments(actor):
    comic = getattr(actor, "comic", None)
    if comic is None:
        return []
    attachments = []
    for link in comic.payment_links or []:
        if not isinstance(link, dict) or not link.get("url"):
            continue
        attachment = {
            "type": "Link",
            "href": link["url"],
            "name": link.get("name") or "Support",
            "rel": link.get("rel") or "payment",
        }
        attachments.append(attachment)
    return attachments


def actor_to_activitypub(actor: Actor) -> dict:
    document = {
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
    attachments = _payment_attachments(actor)
    if attachments:
        document["attachment"] = attachments
    if actor.also_known_as:
        document["alsoKnownAs"] = list(actor.also_known_as)
    if actor.moved_to:
        document["movedTo"] = actor.moved_to
    return document
