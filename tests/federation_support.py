import json
from email.utils import formatdate
from urllib.parse import urlparse

import requests

from federation.http_signatures import content_digest, legacy_digest, sign_cavage
from federation.keys import load_actor_keys
from federation.rfc9421 import sign_rfc9421

SIGNED_HEADERS = ("Date", "Digest", "Signature", "Signature-Input", "Content-Digest")


def signed_headers(actor, url, body, *, scheme="cavage", key_id=None):
    message = requests.Request(
        "POST",
        url,
        data=body,
        headers={
            "Host": urlparse(url).netloc,
            "Date": formatdate(usegmt=True),
            "Content-Type": "application/activity+json",
            "Digest": legacy_digest(body),
            "Content-Digest": content_digest(body),
        },
    ).prepare()
    keys = load_actor_keys(actor)
    key_id = key_id or f"{actor.ap_id}#main-key"
    if scheme == "rfc9421":
        sign_rfc9421(message, keys.rsa_private_key, key_id)
    else:
        message.headers["Signature"] = sign_cavage(
            message, keys.rsa_private_key, key_id
        )
    return message.headers


def http_headers(headers):
    return {
        "HTTP_" + name.upper().replace("-", "_"): headers[name]
        for name in SIGNED_HEADERS
        if name in headers
    }


def post_activity(client, path, activity, actor, *, scheme="cavage", **overrides):
    body = json.dumps(activity).encode("utf-8")
    url = f"http://testserver{path}"
    headers = signed_headers(actor, url, body, scheme=scheme, **overrides)
    return client.post(
        path,
        data=body,
        content_type="application/activity+json",
        **http_headers(headers),
    )
