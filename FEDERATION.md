# Federation

This document (FEP-67ff) describes the ActivityPub capabilities of this server.

## Implemented

- **ActivityPub actor documents** — `application/activity+json` at `{INSTANCE_URL}/actors/{handle}`.
- **WebFinger** — `acct:handle@domain` resolution at `/.well-known/webfinger`.
- **NodeInfo (FEP-f1d5)** — discovery at `/.well-known/nodeinfo`, document at `/nodeinfo/2.1`, schema 2.1.
- **RSA-2048 actor keys** — HTTP Signatures material (signing arrives with federation delivery).
- **Ed25519 actor keys (FEP-521a)** — published via FEP-521a Multikey when activity serialization lands.
- **Page `Note` objects** — published pages serialize to ActivityStreams `Note`s with image attachments at `{INSTANCE_URL}/pages/{id}` (`application/activity+json`); `Create`/`Update`/`Delete` are emitted for federated pages and exposed in the comic actor's outbox.

## Planned

- HTTP Signatures (cavage and RFC 9421 double-knock), object integrity proofs (FEP-8b32), delivery, inbox/outbox, and reply handling.
- Search-indexing consent (FEP-5feb).