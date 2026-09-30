import json
from email.utils import formatdate
from urllib.parse import urlparse

import requests

from actors.models import Actor
from actors.software import SOFTWARE_VERSION
from federation import http_signatures, rfc9421
from federation.keys import load_actor_keys
from federation.models import PeerSignaturePreference, SignatureScheme

ACTIVITYPUB_CONTENT_TYPE = "application/activity+json"
DEFAULT_TIMEOUT = 10
CAVAGE_GET_HEADERS = ["(request-target)", "host", "date"]


def user_agent():
    return f"Panels/{SOFTWARE_VERSION}"


def _base_headers(body):
    return {
        "User-Agent": user_agent(),
        "Accept": ACTIVITYPUB_CONTENT_TYPE,
        "Date": formatdate(usegmt=True),
        "Digest": http_signatures.legacy_digest(body),
        "Content-Digest": http_signatures.content_digest(body),
    }


def _sign(message, actor, scheme, *, has_body):
    keys = load_actor_keys(actor)
    key_id = f"{actor.ap_id}#main-key"
    if scheme == SignatureScheme.RFC9421:
        covered = (
            ("@method", "@target-uri", "content-digest")
            if has_body
            else ("@method", "@target-uri")
        )
        rfc9421.sign_rfc9421(message, keys.rsa_private_key, key_id, covered)
    else:
        headers = None if has_body else CAVAGE_GET_HEADERS
        message.headers["Signature"] = http_signatures.sign_cavage(
            message, keys.rsa_private_key, key_id, headers
        )


def _preference(domain):
    preference = PeerSignaturePreference.objects.filter(domain=domain).first()
    return preference.scheme if preference else None


def _remember(domain, scheme):
    PeerSignaturePreference.objects.update_or_create(
        domain=domain, defaults={"scheme": scheme}
    )


def signed_request(
    method,
    url,
    *,
    body=None,
    actor,
    session=None,
    timeout=DEFAULT_TIMEOUT,
    accept=None,
):
    has_body = body is not None
    payload = json.dumps(body).encode("utf-8") if has_body else None
    domain = urlparse(url).netloc
    remembered = _preference(domain)
    schemes = [SignatureScheme.CAVAGE, SignatureScheme.RFC9421]
    if remembered:
        schemes = [remembered, *[s for s in schemes if s != remembered]]
    session = session or requests.Session()
    last_response = None
    for scheme in schemes:
        headers = _base_headers(payload or b"")
        headers["Host"] = urlparse(url).netloc
        if has_body:
            headers["Content-Type"] = ACTIVITYPUB_CONTENT_TYPE
        if accept:
            headers["Accept"] = accept
        request = requests.Request(method, url, data=payload, headers=headers)
        prepared = request.prepare()
        _sign(prepared, actor, scheme, has_body=has_body)
        last_response = session.send(prepared, timeout=timeout)
        if last_response.status_code != 401:
            if last_response.status_code < 400:
                _remember(domain, scheme)
            return last_response
    return last_response


def post_activity(url, activity, actor, *, session=None, timeout=DEFAULT_TIMEOUT):
    return signed_request(
        "POST", url, body=activity, actor=actor, session=session, timeout=timeout
    )


def fetch_json(url, *, actor=None, session=None, timeout=DEFAULT_TIMEOUT):
    actor = actor or Actor.objects.filter(is_instance_actor=True, domain="").first()
    if actor is None:
        return None
    response = signed_request("GET", url, actor=actor, session=session, timeout=timeout)
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None
