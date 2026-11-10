# Federation

This document (FEP-67ff) describes the ActivityPub capabilities of this server.

## Implemented

- **ActivityPub actor documents** — `application/activity+json` at `{INSTANCE_URL}/actors/{handle}`.
- **WebFinger** — `acct:handle@domain` resolution at `/.well-known/webfinger`.
- **NodeInfo (FEP-f1d5)** — discovery at `/.well-known/nodeinfo`, document at `/nodeinfo/2.1`, schema 2.1.
- **HTTP Signatures** — draft-cavage and RFC 9421 accepted inbound; cavage-first outbound with RFC 9421 fallback on 401, plus Digest/Content-Digest.
- **RSA-2048 actor keys** — required HTTP Signatures material.
- **Ed25519 actor keys (FEP-521a)** — published via FEP-521a Multikey.
- **Delivery, inbox, and outbox** — Django Tasks delivery with retries, shared/actor inboxes, and actor collections.
- **Follower synchronization (FEP-8fcf)** — Collection-Synchronization headers and authenticated partial follower collections.
- **Account portability** — actor `alsoKnownAs`/`movedTo`, outbound `Move`, inbound follow migration, and an account export command.
- **Object integrity proofs (FEP-8b32)** — eddsa-jcs-2022 proofs on outbound activities and served page Notes, verified on inbound activities.
- **WebSub publishing** — configured hub is pinged when comic feeds change, with hub discovery links on comic/reader pages.
- **Page `Note` objects** — published pages serialize to ActivityStreams `Note`s with image attachments at `{INSTANCE_URL}/pages/{id}` (`application/activity+json`); `Create`/`Update`/`Delete` are emitted for federated pages and exposed in the comic actor's outbox.
- **Comment `Note` objects** — replies on pages are stored as threaded `Comment`s and served at `{INSTANCE_URL}/comments/{id}` (`application/activity+json`); inbound `Create`/`Note` replies are accepted.

## Planned

- Search-indexing consent (FEP-5feb), quote/reply policy FEPs, and WebSub.