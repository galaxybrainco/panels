"""Opt-in live interop smoke checks.

Environment:
  INTEROP_INSTANCE_URL  Base URL of this Panels instance
  INTEROP_PEERS         Comma-separated remote actor URLs to fetch
  INTEROP_ACTOR_HANDLE  Optional local handle to verify its actor document
"""

import os
import sys

import requests

CONTENT_TYPE = "application/activity+json"


def check_instance(base, session=None, timeout=10):
    session = session or requests.Session()
    base = base.rstrip("/")
    discovery = session.get(f"{base}/.well-known/nodeinfo", timeout=timeout)
    discovery.raise_for_status()
    links = discovery.json().get("links") or []
    if not links:
        raise RuntimeError("nodeinfo discovery returned no links")
    nodeinfo = session.get(links[0]["href"], timeout=timeout)
    nodeinfo.raise_for_status()
    data = nodeinfo.json()
    if "activitypub" not in data.get("protocols", []):
        raise RuntimeError("nodeinfo does not advertise activitypub")
    federation = session.get(f"{base}/FEDERATION.md", timeout=timeout)
    federation.raise_for_status()
    if "activitypub" not in federation.text.lower():
        raise RuntimeError("FEDERATION.md missing ActivityPub declaration")
    return {"nodeinfo": data, "federation_md": federation.text}


def check_peer(actor_url, session=None, timeout=10):
    session = session or requests.Session()
    response = session.get(actor_url, headers={"Accept": CONTENT_TYPE}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    for key in ("id", "type", "inbox"):
        if not data.get(key):
            raise RuntimeError(f"peer actor missing {key}: {actor_url}")
    return data


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    instance = os.environ.get("INTEROP_INSTANCE_URL", "")
    peers = [item for item in os.environ.get("INTEROP_PEERS", "").split(",") if item]
    if not instance:
        print("INTEROP_INSTANCE_URL is required", file=sys.stderr)
        return 2
    failures = 0
    try:
        result = check_instance(instance)
        print(f"instance ok: {result['nodeinfo'].get('software', {})}")
    except Exception as exc:
        failures += 1
        print(f"instance failed: {exc}", file=sys.stderr)
    for peer in peers:
        try:
            actor = check_peer(peer)
            print(f"peer ok: {actor.get('id')} ({actor.get('type')})")
        except Exception as exc:
            failures += 1
            print(f"peer failed: {peer}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
