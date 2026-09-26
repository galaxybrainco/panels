# Webcomic Platform with Deep ActivityPub Support — Development Plan

*Working title: "Panels" (placeholder). A community-first, multi-tenant Django platform for hosting webcomics that are first-class citizens of the fediverse. This document is written to be handed to a development agent; it states locked decisions, the domain and ActivityPub model, UX scope, phasing, and open risks.*

---

## 1. Vision & guiding principles

Host webcomics that read beautifully on-site **and** behave like well-mannered fediverse accounts, so a reader on Mastodon or Pixelfed can follow a comic, see each new page in their timeline, and reply — with that reply appearing under the page on-site.

Non-negotiable principles, in priority order:

1. **Progressive enhancement over a Mastodon-compatible baseline.** Everything ships in a form Mastodon fully understands; modern AP affordances layer on top so they light up for capable servers and are safely ignored by the rest. Never adopt an extension in a way that breaks Mastodon legibility. Full stance and feature matrix in §10.
2. **Graceful degradation over semantic purity.** A page must render sensibly in Mastodon, Pixelfed, and other AP software even if that means using well-supported object types rather than exotic-but-correct ones.
3. **Accessibility is federated.** Alt text is required at publish; transcripts are first-class. This is both an accessibility duty and good federation hygiene (alt text travels with the image).
4. **Good-citizen federation.** Correct `Update`/`Delete` propagation, HTTP signatures in and out, content warnings, report handling, defederation tooling, and account portability.
5. **Never leak paid content.** Gated content is never delivered to a remote inbox. See §7.

---

## 2. Locked decisions (the frame)

| Decision | Choice |
|---|---|
| Deployment | Community-first multi-tenant; single-creator mode is a phase-2 config |
| AP layer | Greenfield; compose primitives + model on reference apps (no mature Django AP library exists) |
| Runtime | Sync Django 6.0+; background work via Django Tasks (`django.tasks`), DB-backed worker to start, Redis backend when volume grows — no Celery |
| Comic formats | Paged series **and** standalone gag strips (both reduce to "a page = one or more images") |
| Actor model | Comic = featured followable `Person`/`Organization` actor; every user = `Person` actor (followable but not promoted in v1; interactions-only outbox). Creator-follow UI is a v2 toggle |
| Comments | Fediverse replies **are** the comment section (native AP) + local replies; per-page moderation required |
| Reader accounts | Full local accounts: personal feed, reading progress, notifications |
| Maturity | **SFW / all-ages only in v1.** Content warnings + `sensitive` flag retained for non-adult sensitive content (violence, spoilers, flashing). Adult-content support (age-gate, explicit ratings, verification) deferred to a later phase |
| Monetization | Member-only / early-access pages as a core v1 feature. Member payments **off-platform** via Ko-fi + Buy Me a Coffee; only first-party charge is a flat **per-comic** plan ($10/mo, 10 GB each) |

---

## 3. Stack & architecture

- **Django 6.0+** (sync/WSGI), **PostgreSQL**. Background work uses Django's **built-in Tasks framework** (`django.tasks`: `@task` + `.enqueue()`) for delivery, inbound processing, thumbnailing, scheduled publishes, and reply-tree crawling.
- **Task execution — the caveat:** Django 6.0 ships the Tasks *API* but only dev backends (Immediate/Dummy); it does **not** ship a production worker. Start with the reference **`django-tasks`** database-backed backend (Postgres as the queue, durable enqueue + retries, a `db_worker` management command) — zero extra infra. Because task code is backend-agnostic, swap in **`django-tasks-redis`** (Redis Streams, consumer groups, priorities, crash recovery) as fan-out volume grows, with no changes to the tasks themselves. This satisfies "Django Tasks wherever possible": only the *backend* is an infra choice, not the code.
- **Scheduling:** one-off scheduled publishes use delayed/`run_after` enqueue; recurring sweeps (retry drains, reply-crawl, quota rollups) run from a cron/systemd-timer management command — no Celery beat.
- **Redis** is optional at first (cache; later the task backend), not a day-one hard dependency.
- **Media & delivery:** **Bunny Storage** as origin + **Bunny CDN** pull zone with aggressive edge caching — long/immutable `Cache-Control` on page images so the origin is barely touched and egress stays near-zero (the economic keystone of the billing model, §7). `libvips` (prefer `pyvips`) for derivatives. Per-comic storage accounting for the 10 GB quota (§7). Cached **remote** media (avatars, reply attachments) is served/cached but **not** counted against any quota — it's engagement-driven, so charging for it would contradict the "don't charge for success" principle — with TTL-based eviction so it can't grow unbounded.
- **ActivityPub primitives:** no production-grade reusable Django AP library exists as of 2026 (`django-activitypub` is alpha/abandoned; the `fedi-libs` toolkit was archived Sept 2026). Compose focused libraries for the hard parts — HTTP Signatures (cavage + RFC 9421), ActivityStreams 2.0 / JSON-LD (de)serialization, WebFinger, NodeInfo — and build the models, views, and delivery tasks yourself (§10 for the signing/interop stack).
- **Reference implementations to read closely (not depend on):**
  - **Bookwyrm** — the closest precedent: a *non-microblog* AP app with custom objects and Mastodon fallbacks. Study its object serialization and federation flow.
  - **Takahē** (Andrew Godwin) — clean, modern Django AP plumbing (signing, delivery, actor handling) and, notably, a **unified identity table** for local + remote actors. Study its `User`↔`Identity` split (see §4) and its "stator" task model.
- **`bovine`** is the one general Python AP toolkit still maintained (WebFinger, NodeInfo, HTTP sigs, AS2). It's async-first; use it as a source of primitives/reference rather than adopting it wholesale, to keep the sync + Django-Tasks model.

**Suggested Django apps:** `accounts` (local Users + auth), `actors` (the unified Actor/Instance tables + keys, §4), `comics` (comics, series, chapters, pages), `media` (uploads + derivatives + quota), `federation` (AP core: signing, delivery, inbox, collections), `social` (follows, likes, boosts, comments/replies, notifications, reading progress), `memberships` (Ko-fi/BMAC linking + supporter status + per-comic plan/quota), `moderation` (reports, blocks, mutes, defederation), `feeds` (RSS/Atom, sitemaps, oEmbed/OpenGraph).

---

## 4. Domain model

Core entities (fields abbreviated). The spine is a **single unified `Actor` table** for every AP identity — local and remote alike — so the social graph and moderation never branch on origin. This is the same call Mastodon makes (its `accounts` table stores local and remote accounts, local rows just having a null `domain`) and Takahē makes (one `Identity` table). It's a better fit than a separate `RemoteActor` table precisely because we dogfood AP internally: a local follow/like/reply is `Actor → Actor` exactly like a remote one, and a mod action targets an `Actor` (or `Instance`) whether it's local or across the fediverse.

- **Actor** (unified, local + remote) — one row per AP identity. `ap_id` (URI, unique), `type` (`Person` / `Service` / `Group` / `Organization`), `handle` (preferredUsername), `domain` (**null/empty ⇒ local**, else the remote host), `inbox`, `shared_inbox`, `outbox`, `followers`, `following`, `featured`, `public_key_pem` (RSA-2048), `ed25519_public_key` (FEP-521a); for local actors, the encrypted private keys; profile (`name`, `summary`, avatar, header), `manually_approves_followers`, `indexable`/`discoverable` (FEP-5feb), moderation state (see AccountModeration), `last_fetched_at` (remote refresh), timestamps. **Every** Follow/Like/Announce/Block/Mute/Report/Comment references `Actor`.
- **User** (local auth only) — email, credentials, **passkeys/WebAuthn**, TOTP, sessions, preferences, creator-plan billing, roles. `OneToOne → Actor` (the user's `Person` actor). This separates *authentication* (User) from *identity* (Actor), mirroring Takahē's `User`/`Identity` split; remote people have an `Actor` and no `User`.
- **Comic** — `OneToOne → Actor` (a `Person`/`Organization` actor, its own handle `@comic@instance`, avatar/banner), `content_rating` (all-ages / teen — explicit deferred), `update_schedule`, `default_federation` (federated / local-only), description, tags. **Managed by multiple users via `ComicRole`** (below). Remote comic-like actors are just `Actor` rows with no local `Comic`.
- **ComicRole** — a through-model (User ↔ Comic) that makes multi-user management first-class, with escalating roles: **owner** (billing + full control — settings, actor profile, invite/remove collaborators, transfer ownership, delete; responsible for the comic's plan + billing, §7), **editor** (create/edit/publish/schedule pages, manage series & chapters, moderate replies), **contributor** (create/edit *drafts* only — cannot publish; for guest artists/writers), **moderator** (reply moderation + commenter bans on this comic only). One billing owner per comic; any number of other collaborators. Permission checks key off this role everywhere (publish, settings, moderation).
- **Instance** (unified, local + remote) — `domain`, software/NodeInfo, `shared_inbox`, moderation flags (blocked / silenced / allowlisted / reject-media / reject-reports). The local instance is one such row (or config).
- **Series / Chapter / Page** — a Comic has one or more Series; a Series optionally has Chapters; a Chapter/Series has ordered Pages. A **gag comic** is just a Series where each Page is standalone. A **Page** has: ordered Media, `alt_text` (required), optional `transcript`, optional `author_commentary`, `published_at`, `scheduled_for`, `audience` (public / unlisted / followers-only / members / tier) **and** `federation` (federated / local-only) as independent settings, `content_warning`, `sensitive` bool, its own AP object id, and the **publishing User** recorded internally for credit/audit and on-site bylines (it still federates `attributedTo` the comic actor — one federated identity, many humans behind it).
- **Media** — original upload + derivatives (responsive webp/avif, thumbnail, federation-capped variant). Dimensions, hash, content flags, and **bytes counted against the comic's storage quota** (§7).
- **SupporterLink / SupporterStatus** — links a reader `Actor` (local or remote) to an off-platform Ko-fi/BuyMeACoffee supporter (by verified email), plus current tier + validity window. Grants member access; no on-platform payment (§7).
- **ComicPlan** — the one first-party subscription, attached to a **Comic** (not a user): flat plan + 10 GB quota + usage, paid by the comic's owner. A creator with N comics has N independent plans (§7).
- **Follow / Like / Boost(Announce)** — `Actor → Actor`/object; local *and* remote modeled uniformly as AP activities (one code path).
- **Comment** — a reply object (`inReplyTo` a Page's AP id or another Comment), authored by an `Actor`. Local or remote. Threaded. Moderation state (visible / hidden / pending / reported).
- **Notification** — new page from a followed comic, reply to your comment, like/boost, new follower.
- **ReadingProgress** — per (local) User per Series: last page read, timestamp (powers "resume").
- **Report(Flag)** — inbound and outbound moderation reports on an `Actor`/object; assignable, with resolution state and moderator comments.
- **Mute / Block** — a viewer `Actor`'s relationship to another `Actor`. Mute is local-only (optionally notification-muting, optionally time-limited). Block removes follows both ways and emits a `Block` activity.
- **DomainBlock** — scoped either to a user (personal instance block) or the server (admin defederation), with a mode (suspend / silence / reject-media / reject-reports); targets an `Instance`.
- **Filter** — per-user keyword/phrase filters (scope + optional expiry); comics can also carry auto-hide keywords for replies.
- **AccountModeration** — admin state on any `Actor` (silenced / suspended / frozen / disabled), reason, expiry.
- **ModeratorAction** — append-only audit log of every mod action (actor, target, action, reason, timestamp); plus free-form **moderation notes** on actors.

> **Dogfooding note:** treat local follows, likes, and replies as ActivityPub activities internally. Local-vs-remote then stops being a special case, and correctness improves.

---

## 5. ActivityPub: the core

### Actors
- Comic actor type: **`Person`** (or `Organization`) — deliberately *not* `Service` (earns a "bot" badge and gets filtered from some feeds) or `Group` (boost-everything forum semantics you don't want). Pages are `attributedTo` the comic actor.
- Every user is a `Person` actor (required because they participate natively in reply/like/boost threads).
- **Follow-target stance (v1): comics are the featured follow target; user actors are followable but not promoted.** With the grain of ActivityPub, every actor is a first-class followable peer — so user `Person` actors do `Accept` incoming `Follow`s and their replies/likes/boosts federate normally. But the product features only *comic*-follows: there is **no "follow this creator" UI and no personal-feed-of-people** in v1. A user actor's `outbox` therefore contains their **interactions** (replies, likes, boosts) and any personal announcements — **not** pages, which are `attributedTo` the comic. This is the open-ended foundation: surfacing creator-follows later (a featured profile + follow button) is a pure UI addition in v2 with **zero protocol change**. Do not hard-lock user actors (that would mean auto-`Reject`ing valid follows and fighting the network) and do not build a personal timeline of followed people.
- Each actor: RSA-2048 keypair (plus an Ed25519 key via FEP-521a — §10), WebFinger (`acct:comic@instance`), `inbox`, `outbox`, `followers`, `following`, `featured` (pinned), and an `endpoints.sharedInbox`.
- **NodeInfo 2.1** + a `/.well-known/nodeinfo` document advertising software name, version, open-registration status, and (optionally) content policy.
- **Instance actor** for signed fetches.

### Objects — a page
- Primary type: **`Note`** with the page image(s) as `attachment` (each with `name` = alt text), a short `content` (title + first line of commentary + link back to the on-site reader), `url` back to the page, `sensitive` + `summary` (the content warning) when mature.
  - This renders as a normal image post in Mastodon and as an image post/album in Pixelfed (multi-panel pages ≈ Pixelfed albums).
- Consider **`Article`** only for commentary-heavy pages (long author notes); it renders as a titled card in Mastodon. Recommendation: ship `Note` first; add `Article` as an option later. Always include full context in a form that degrades cleanly.
- **Federation-capped image:** federate a size/dimension-capped derivative (Mastodon re-encodes and caps dimensions ~4096px / a few MB); link back to full-res on-site. Never assume remote servers preserve your original.

### Visibility, addressing & federation
Two independent axes decide who can see a page and whether it leaves the instance.

**Audience** (maps to ActivityPub addressing):
- **Public** (default) — `to: as:Public`, `cc: followers`. On public + federated timelines, discoverable, boostable.
- **Unlisted** — `cc: as:Public` (not in `to`), `to: followers`. Reaches followers' feeds and anyone with the link, but excluded from public/federated timelines and discovery.
- **Followers-only** — addressed to the followers collection only. Delivered to followers (local + remote); not public, not publicly boostable. **Privacy is by convention:** remote servers honour it but it isn't enforced, so don't treat followers-only as secret.
- **Members / Tier** — paid gate, enforced on-site; the page's content **never federates** (a public teaser `Note` federates instead, §7).

**Federation** (orthogonal toggle):
- **Federated** (default) — deliver per the audience's addressing.
- **Local-only** — reads normally on-site but is **never delivered to any remote inbox and never appears in the comic's public `outbox`/`featured`.** No `Create`/`Update`/`Delete` is emitted. Overrides the audience for delivery purposes.

Centralise the decision in one **`federation_plan(page)`** that returns both *whether* to emit an activity and *how to address it*:
- Members/Tier, or Local-only → emit nothing (the paid case federates a teaser instead).
- Public / Unlisted / Followers-only + Federated → emit a `Create` addressed per the audience and delivered to the right inboxes; only Public is listed on public timelines and discovery.

Precision for the agent: local-only means "not delivered via ActivityPub," not "secret" — a public + local-only page is still public HTML that can be read or scraped directly. Real privacy comes from audience (followers-only best-effort, or members/tier enforced on-site), not the federation toggle. On-site, Public and Unlisted both appear in the comic's archive; followers-only and members/tier are gated. Public RSS/feeds include Public (and Unlisted by direct link) but exclude followers-only and members/tier.

### Activities to implement
`Create`, `Update` (edits/typo fixes on a page must propagate), `Delete` (unpublish/removal must propagate), `Follow`/`Accept`/`Reject`, `Undo` (unfollow, unlike, unboost), `Like`, `Announce` (boost), `Flag` (reports, in and out), `Move` + `Also-Known-As` (account/comic portability — phase 2 acceptable).

### Delivery & inbound
- **Outbound:** sign with HTTP Signatures (cavage-first double-knock + RFC 9421, §10); deliver to shared inboxes where available; one Django Tasks job per delivery with retry + exponential backoff + dead-lettering; deduplicate by shared inbox.
- **Inbound:** verify signatures (accept both cavage and RFC 9421); support **authorized fetch / secure mode** (many instances require it); reject/queue by instance policy.
- **Collections** paginate correctly (`OrderedCollection` + pages).
- **Rate-limit for cost and abuse.** Because the operator eats engagement cost by design (delivery fan-out, inbound reply/like/boost processing, a new `Actor` row per new remote participant, reply-tree crawling), cap it so it can't be weaponized: throttle outbound fan-out, inbound processing per remote actor/instance, remote-media fetches, and crawl depth/rate. A paid comic with uncapped fan-out is an amplification vector — bound it.

### Comments = inbound replies (the honest caveat)
- Replies arrive addressed to a page's `Note`. Display as the comment section, threaded via `inReplyTo`.
- **Incomplete-thread problem:** you only reliably receive replies from instances that deliver to you. To show fuller threads, crawl the `replies` collection and walk `inReplyTo` chains (background task, rate-limited, cached). Accept that completeness is never guaranteed and design the UI to not imply it is.
- **Per-page reply moderation is a v1 requirement:** hide/block/report a reply, block an actor or instance, and an optional **approve-before-show** mode per comic. Every page is now an inbox for the whole fediverse — plan for spam and harassment from day one.

### Federation policy
- **Open by default** with a blocklist + domain-block/defederation tooling (fediverse norm).
- **Allowlist mode** as a config toggle for locked-down/curated instances.
- Instance-level silence and suspend, mirroring Mastodon semantics so admins have familiar controls. Full moderation toolset in §9.

---

## 6. Reader & creator UX

### Reader
- **Reader view:** clean paged navigation (first / prev / next / latest), keyboard nav (←/→), mobile swipe, next-page preloading. No infinite vertical webtoon mode (out of scope by decision).
- **Archive:** by series/chapter, thumbnails, jump-to-date, storyline navigation.
- **Reading progress / resume:** "continue where you left off" per series across long archives.
- **Personal feed:** new pages from followed comics + notifications (replies, likes/boosts, new pages).
- **Discovery:** on-instance browse/search, tags, featured/staff picks; respect maturity filters.
- **Alt text + transcript** surfaced accessibly (screen-reader friendly, expandable transcript).

### Creator
- **Dashboard** per comic: pages, schedule calendar, members, moderation queue, basic stats (views, followers local + remote).
- **Authoring:** upload one or more images per page; **alt text required before publish**; transcript field; rich author-commentary block; tags; content warning + maturity flag.
- **Scheduling:** draft → schedule → auto-publish + `Create`/deliver on a cadence (e.g. MWF). One engine powers both public scheduling and member early-access embargoes (§7).
- **Publishing controls:** per-page **audience** (public / unlisted / followers-only / members / tier) and **federation** (federated / **local-only**), with a per-comic default. Local-only pages read normally on-site but are never pushed to the fediverse (§5) — a first-class, one-tap setting, not a hidden flag.
- **Collaboration:** invite users to a comic and assign roles (owner / editor / contributor / moderator, §4); manage or revoke access; see who authored and published each page. Pages publish as the comic actor while crediting the human author on-site.
- **Reply moderation** inline on each page.

---

## 7. Membership & federation (the hard part — read carefully)

ActivityPub has no DRM. Once an object is delivered to a remote inbox it is out of your control, and "paying member" cannot be mapped to any AP audience. Therefore **gated content is never federated.** Two supported patterns, both federation-safe:

1. **Early-access (drip) — the common case.** The page publishes to members immediately (`audience = members`/`tier`), but its **public + federated release is scheduled for later** via the scheduling engine. When the embargo lifts, the page flips to public and the `Create` is delivered to followers. Most patrons want *early* access, not permanent exclusivity, so this covers the majority case and federates cleanly on release.
2. **Permanently exclusive.** The full page lives on-site behind auth and **never federates**. Optionally federate a **public teaser** `Note` (e.g. first panel + "members read the rest → link") so followers still see activity and a call to action. The teaser is public; the page is not.

**Enforcement rules for the agent:**
- The federation layer emits a page `Create` only for **federated** pages whose audience is **public, unlisted, or followers-only**; **members/tier (paid) and local-only pages emit nothing** (the paid case federates a teaser instead). Centralise this in the one `federation_plan(page)` from §5 — it returns both *whether* to federate and *how to address it* — and call it from the serializer, not just the view.
- Membership grants on-site read access only; it never changes AP addressing.

**Membership payments are off-platform in v1.** The platform processes **no** member payments. Members pay creators directly on **Ko-fi** or **Buy Me a Coffee**; we ingest that support and grant on-site access. The platform's own (and only first-party) revenue is a flat **per-comic plan: $10/month for up to 10 GB of uploads, per comic.**

How supporter access works:
- A creator connects their Ko-fi and/or BMAC account to their comic.
- We learn who supports them two ways, because the two services differ sharply:
  - **Ko-fi** is **webhook-only** — a single "Payment Received" webhook POSTs on each tip/subscription payment (with `tier_name`, supporter email, `is_subscription_payment`, and a `verification_token` to authenticate it). There is **no query API and no reliable cancellation event**, so membership state is event-sourced: a subscriber is "active" until a renewal payment fails to arrive within their cycle (+grace). Design around missed-renewal lapse, not a cancel signal.
  - **Buy Me a Coffee** exposes an **API** (list members/subscriptions with email, tier, status) plus membership webhooks — so use webhooks for immediacy and **periodically reconcile** against the API for truth.
- **Identity linking (the crux):** map an off-platform supporter (identified by email) to a reader `Actor`. The reader links and verifies their Ko-fi/BMAC email; on match we attach a `SupporterLink` + `SupporterStatus` (tier + validity). This is what makes it work for **remote fediverse readers too** — a `userB@other.server` links their Ko-fi email exactly like a local user, no cross-instance payment plumbing needed.
- **Access** = "has an active `SupporterStatus` at the required tier for this comic." Enforced on-site only; it never changes AP addressing.

**Per-comic plan & quota (first-party):**

*Design intent: recover storage + hosting cost with a comfortable margin, without charging for success (traffic or engagement).* That only holds if **egress is near-zero**, so the plan is built on **Bunny CDN + Bunny Storage** with aggressive edge caching (§3): storage becomes the dominant marginal cost, which is exactly what the flat fee recovers. Serve popular comics at near-zero marginal cost and the model is honest cost-recovery; serve them off naive S3-style egress and popular comics get silently subsidized by everyone else — the opposite of what a flat fee implies.

- Flat **$10/month, 10 GB per comic** via **Stripe Billing** (the one first-party payment). The plan and quota attach to the **Comic** (`ComicPlan`, §4), paid by its **owner** (`ComicRole=owner`); a creator running N comics pays N × $10 for N × 10 GB. Collaborators pay nothing. Transferring ownership reassigns the payer (move/recreate the subscription under the new owner's Stripe customer).
- **Storage add-ons above 10 GB, not a hard wall.** A decade-long archive driving storage is the one "success" it's fair to charge for under this philosophy — because it's *bytes*, not readers. Offer paid storage tiers/add-ons; hitting the cap prompts an upgrade, never a dead end.
- **Annual option.** Stripe's ~$0.30 + 2.9% is ~6% on a $10 charge; an annual plan meaningfully protects the margin the flat fee is meant to preserve.
- **Enforcement:** sum stored bytes **per comic**, block uploads over that comic's cap with an upgrade prompt. *(Open decision: whether generated derivatives count toward the quota or only originals — recommend originals only, so creators aren't taxed for our encoding choices.)*
- **Operator-configurable.** Price, cap, storage tiers, and *whether billing exists at all* are deploy config — the single-creator self-host mode (phase 2) turns billing off entirely; another operator on cheaper/pricier infra sets a different number. Nothing here is hardcoded.
- *(Open decision: Stripe Billing vs a merchant-of-record like Paddle/Lemon Squeezy for global sales-tax/VAT.)*

**Plan-lapse lifecycle (distinct from member lapse).** When a *comic's own plan* stops paying, you **cannot** just delete the comic — that fires `Delete` activities, breaks every follow, and rots every link anyone ever shared. Instead, an explicit ladder: **dunning** (retry + notify) → **grace period** → **frozen/read-only** (no new uploads, but the comic still serves and federates existing pages) → eventual **archive with export**. The blast radius of deleting federated content is the thing to design around; freezing preserves the fediverse's links and follows while still applying billing pressure. (This is separate from the Ko-fi/BMAC *member* lapse in the section above, which is inferred from missed renewals.)

The **federation mechanics above are unchanged** by going off-platform: gated pages still never federate, early-access still rides the scheduler, exclusive pages still federate only a teaser.

---

## 8. Content sensitivity (SFW-only in v1)

**v1 is SFW / all-ages only** — no adult or explicit content, no age-gating. This is a deliberate scope and risk reduction: it removes the project's heaviest legal surface (age verification, 2257-type record-keeping, adult-content compliance) from the initial launch.

Still build these, because they matter even for entirely non-adult content:

- **Content warnings + `sensitive` flag** for *non-adult* sensitive material — violence, gore, spoilers, flashing/strobing imagery, body horror. Standard fediverse courtesy; un-CW'd sensitive content still gets instances silenced. Enforce a CW before a flagged page can publish or federate.
- **Per-comic content rating** limited to all-ages / teen; drives discovery filtering. (Explicit ratings deferred.)
- **Instance policy** declared in NodeInfo/about as SFW, so other admins can make informed federation choices.
- **Baseline trust & safety** (independent of the SFW decision): any platform accepting image uploads needs a clear report → review → takedown path, and should weigh CSAM hash-matching on upload as a prudent minimum. Turning NSFW off reduces this surface but does not eliminate it — bad actors can upload illegal content to any open instance.

Accounts likely **do not need DOB / age attestation** in v1 (that existed for age-gating) — drop it unless another feature needs it.

**Deferred to a later "adult-content mode" phase:** age-gate + DOB/verification, explicit per-comic ratings, a per-deploy adult-content toggle, mandatory CSAM scanning tied to adult uploads, and the associated legal/compliance work (get qualified legal advice before enabling). Requirements are preserved here so that phase starts from a known scope.

---

## 9. Moderation & safety (Mastodon-parity)

Three tiers of control: **each user** protects their own experience, **comic owners** moderate their own comic's space, and **instance admins/moderators** govern the whole server. Target parity with Mastodon's toolset.

### User level (self-service)
- **Mute account** — hide a user's pages and replies from your feeds; option to also mute their notifications; optional time-limited mutes (e.g. 1 day / 7 days / until removed). Mutes are **local-only and never federate.**
- **Block account** — remove any follow relationship in both directions, stop them seeing or interacting with you, and hide their content from you. Emits a `Block` (and `Undo Block`) so the remote server enforces it; blocks are not publicly advertised.
- **Mute a thread** — silence notifications for one page's comment thread.
- **Keyword / phrase filters** — hide matching content from your feeds, with scope and optional expiry (Mastodon-style filters).
- **Personal domain block** — a user can hide *everything* from a chosen remote instance for themselves. Local-only.
- **Remove follower** — drop a follower without a full block.
- **Report** — flag an account, page, or comment; optionally forward the report to the offender's home instance.

### Comic-owner level (moderating your own comic)
- **Block / ban a commenter** (local or remote actor) from replying on your comic. **Local-only for now:** it never emits a `Block` from the comic actor and isn't advertised — it simply stops that actor's replies from appearing on the comic.
- **Reply moderation** per page: hide, approve-before-show, bulk actions (§6).
- **Per-comic muted keywords** to auto-hide matching replies.
- A **reports/flagged-replies queue** scoped to the owner's comics.

### Instance / admin & moderator level
Account actions (apply to **local and remote** accounts):
- **Silence / limit** — hide from public and federated timelines and discovery; only followers see them.
- **Suspend** — halt all federation with the account and withhold/remove its content; reversible window before hard deletion.
- **Freeze** (local only) — can log in but cannot post.
- **Warn / disable / delete** (local) and **force-sensitive**, each with a reason delivered to the user.
- **Moderation notes** on accounts and an append-only **audit log** of every action.

Domain / instance actions (defederation suite):
- **Suspend domain** — full defederation: reject all activity and purge/withhold that instance's data.
- **Silence domain** — limited federation: content hidden from public timelines, visible only via follow.
- **Reject media** from a domain (accept text, drop images).
- **Reject reports** from a domain.
- **Allowlist entries** when running in allowlist mode (§5).
- **Email-domain and IP blocks** for registration abuse.

Reports queue:
- One inbox for **local and inbound remote (`Flag`) reports**; assign to a moderator, comment, resolve, and action inline; **forward** to the offender's home instance; emit an outbound `Flag` when a local user reports a remote account.

### Local vs federated (implementation notes for the agent)
- **Mutes, keyword filters, personal domain blocks, and comic-owner commenter bans are local-only** — never emit an activity.
- **Blocks** emit `Block` / `Undo Block`, and inbound blocks must be enforced.
- **Domain suspends/silences and admin account actions are local instance policy**, enforced in the delivery + inbox layer, not federated as activities.
- Enforce everything at **one gate** — inbox acceptance plus the timeline/query filters — so local and remote content pass the same checks. Because every identity is a row in the one `Actor` table (and every host an `Instance`), a block/mute/silence/suspend is a single lookup with no local-vs-remote branch (§4).

---

## 10. Interoperability & modern-AP posture

**Governing stance: progressive enhancement over a Mastodon-compatible baseline.** Everything ships in a form Mastodon (the majority of the fediverse) fully understands; modern affordances layer on top so they light up for capable servers and are safely ignored by the rest. Never adopt an extension in a way that breaks Mastodon legibility.

### Signatures & keys
- **HTTP signatures — double-knock.** The dominant baseline is the expired-but-ubiquitous `draft-cavage-http-signatures-12` (Pleroma, Akkoma, Misskey, GoToSocial, Lemmy); **RFC 9421** (HTTP Message Signatures) is the finalized standard that Mastodon 4.4+ verifies and 4.5+ can emit. Implement **double-knocking**: sign with one, fall back on `401`, cache the peer's preference; accept both inbound. **Emit cavage-first for now** (as Mastodon itself still does in 2026) and flip the default to RFC 9421 as adoption grows.
- **Keys.** RSA-2048+ is **required** for interop (per the W3C SocialCG *ActivityPub and HTTP Signatures* report). Also publish an **Ed25519** key via **FEP-521a** Multikey (Mastodon 4.7 reads both) and prefer it where the peer supports it.
- **Content-Digest** per RFC 9530, signed on POSTs.

### Object integrity (forwarded activities)
- Attach **FEP-8b32 Object Integrity Proofs** (Data Integrity, `eddsa-jcs-2022`) so relayed edits/deletes/boosts verify without re-fetching the origin — Mastodon 4.7 verifies these. Keep emitting **RsaSignature2017 LD-Signatures** as the legacy fallback for older servers. (Post-quantum `mldsa44-jcs-2024` is emerging in Mastodon 4.7 — worth being key-storage-ready, not required.)

### Rich interactions (consent-respecting, degrade-safe)
- **Quote-sharing a page** — implement Mastodon-4.5-compatible quotes: an **FEP-e232 Object Link** to the quoted object plus **FEP-044f** `QuoteAuthorization` stamps with the `Quote` / `Approve` / `Reject` handshake, so a comic owner controls whether and by whom their pages may be quoted.
- **Reply controls** — **FEP-5624 per-object reply policies** to express who may reply to a page; wire this straight into comment moderation (e.g. followers-only replies or approval-required).
- **Search-indexing consent** — **FEP-5feb**, so pages/actors opt in or out of remote search indexing (pairs with the visibility ladder and local-only in §5).
- **Threading context** — **FEP-7888 `context`** to help peers assemble fuller comment threads (partial mitigation of the reply-completeness caveat in §5).
- **Support links** — expose tip/support links via **FEP-0ea0 Payment Links** on the comic actor, complementing the on-site membership flow (§7).

### Declare what you support
- Ship a **FEDERATION.md (FEP-67ff)** enumerating every FEP/extension the server implements, alongside **NodeInfo (FEP-f1d5)** and **followers-collection sync (FEP-8fcf)**. This is the canonical good-citizen artifact and the first thing other admins check.

### Modern web accounts & auth
- **Passwordless-first:** passkeys / WebAuthn as the primary credential (a passkey satisfies MFA on its own), with TOTP 2FA and email recovery as fallback — the 2026 baseline, already shipping in fediverse servers.
- **OAuth 2.1 / OIDC** for sessions and any API; if you expose a **Mastodon-compatible client API**, act as a Mastodon-style OAuth provider so existing fedi clients can authenticate.
- **No universal "sign in with the fediverse" standard exists** — don't design around one. For cross-instance identity (e.g. remote supporters), use a signed **inbox-challenge handshake** or per-instance OAuth, not a mythical SSO.
- **Portability:** account export + `Move` / `alsoKnownAs` (§5), so users own their identity and can leave.

### Test against the spread
Validate every federation change against three peers, not one: **Mastodon 4.7** (modern; verifies FEP-8b32), a **cavage-only** server (GoToSocial or Akkoma), and a **modern non-Mastodon** server (Mitra or a Fedify app). Passing all three is the bar for "good citizen."

---

## 11. Non-AP niceties (good-citizen + adoption)

- **RSS/Atom per comic** — kept first-class; webcomic audiences live on feeds. (WebSub optional later.)
- **OpenGraph + oEmbed** so links unfurl nicely on-fediverse and off.
- **Sitemaps**, canonical URLs, clean permalinks per page.
- **Account/comic portability:** `Move` activity + full data export (phase 2).
- **Import** from ComicFury / Webtoon / Tapas / RSS / bulk image upload (phase 2; big adoption lever).

---

## 12. Phasing

**v1 (MVP that is a real fediverse citizen)**
- Accounts as Person actors; comics as Person actors; WebFinger, NodeInfo, keypairs, instance actor.
- Series/chapter/page model; paged reader + gag strips; archive; reading progress.
- Publish + scheduling; per-page audience (public / unlisted / followers-only / members / tier) + **federated/local-only** setting; alt text required; transcript + commentary.
- Multi-user comics: `ComicRole` (owner / editor / contributor / moderator), invites + access management, per-page author credit.
- Follow model: comics are the featured follow target; user actors are followable-but-not-promoted with an interactions-only outbox (no creator-follow UI, no personal feed of people).
- Federation: `Create`/`Update`/`Delete`/`Follow`/`Accept`/`Undo`/`Like`/`Announce`; signed delivery with retries; secure-mode inbound.
- Interop baseline (§10): Mastodon-legible `Note`s; cavage signatures with accept-both + double-knock; RSA-2048 keys; Ed25519 via FEP-521a; FEP-8b32 proof emit; RsaSignature2017 fallback; FEDERATION.md + NodeInfo.
- Accounts/auth (§10): passkeys/WebAuthn passwordless-first + TOTP 2FA; OAuth 2.1/OIDC.
- Comments via inbound replies + local replies; per-page moderation incl. approve-before-show; reply-tree crawl (best-effort).
- Memberships: Ko-fi + BMAC linking (webhooks + BMAC reconcile), email→Actor verification, supporter status → gating; early-access via scheduling; exclusive-with-teaser; serializer-level gating. Per-comic plan ($10/mo, 10 GB each) via Stripe Billing + per-comic quota enforcement; storage add-ons above cap; plan-lapse lifecycle (dunning → grace → freeze, never delete); operator-configurable pricing.
- Content sensitivity: SFW/all-ages only; content warnings + `sensitive` flag for non-adult sensitive content; baseline report/takedown path.
- Federation policy: open + blocklist + defederation tools; allowlist toggle.
- Moderation suite (Mastodon-parity, §9): user mute/block/filters/personal domain block; comic-owner reply moderation + commenter bans; admin account silence/suspend/freeze + domain suspend/silence/reject-media; unified reports queue with forwarding; audit log.
- RSS/Atom, OpenGraph/oEmbed, sitemaps.
- Personal feed + notifications.

**v2**
- Single-creator deployment mode.
- **Featured creator profiles + follow UI** (surfacing the already-followable user actors from §5 — a UI addition, no protocol change).
- **Adult-content mode:** age-gate + DOB/verification, explicit ratings, per-deploy toggle, CSAM scanning, and the legal/compliance work it requires.
- Import/migration tooling; `Move` + export.
- `Article` object option for commentary-heavy pages; richer discovery/relays; Patreon/Ko-fi membership ingestion; analytics.
- Rich-interaction enhancements (§10): consent-respecting quote posts (FEP-e232 + FEP-044f), per-object reply controls (FEP-5624), search-indexing consent (FEP-5feb), threading context (FEP-7888), payment/support links (FEP-0ea0); default outbound signatures flipped to RFC 9421 as adoption warrants.

---

## 13. Key risks & open decisions

- **Paid content leakage** — mitigated by serializer-level gating (§7); must be tested explicitly (assert gated pages never produce a deliverable activity).
- **Trust & safety (reduced, not gone)** — SFW-only removes adult-content legal surface from v1, but an open upload platform still needs a report/takedown path and should weigh CSAM hash-matching. Full adult-content compliance moves to the deferred "adult-content mode" phase (§8).
- **Thread completeness** — inbound replies are inherently partial; UI must not imply completeness.
- **Sync vs async** — plan assumes sync Django + Django Tasks (DB backend first, Redis backend to scale fan-out); revisit only if you adopt `bovine`/Takahē-style async wholesale.
- **Off-platform membership** — Ko-fi is webhook-only with no cancel event, so lapse is inferred from missed renewals (tune the grace window); email→Actor linking is the fragile step (unverified/mismatched emails, remote readers). Reconcile BMAC against its API; treat Ko-fi as eventually-consistent.
- **Storage quota** — needs reliable per-comic byte accounting and overage UX; decide whether derivatives count (recommended: originals only).
- **Creator billing provider** — Stripe Billing assumed; a merchant-of-record (Paddle/Lemon Squeezy) is worth considering for global tax.
- **Moderation load** — open replies from the whole fediverse; ensure tooling and rate limits ship in v1, not later.
- **Egress economics (the model's keystone)** — the flat storage-only fee is honest cost-recovery *only* while egress is near-zero. Committed to Bunny CDN/Storage with aggressive edge caching (§3); a naive S3-egress setup would silently subsidize popular comics and break the "don't charge for success" premise. Monitor cache-hit ratio as a first-class metric.
- **Plan-lapse lifecycle** — never hard-delete a lapsed comic (it fires `Delete`, breaks follows, rots shared links). Ladder: dunning → grace → frozen/read-only (still serves + federates) → archive with export (§7).
- **Federation engagement cost** — engagement is operator-funded by design, so it must be rate-limited (fan-out, inbound processing, remote-media fetch, crawl) or it becomes an amplification/DoS vector (§5).
- **Collaborator permissions** — with multi-user comics, enforce `ComicRole` checks consistently across publish, settings, billing, and moderation; a mis-scoped role (e.g. a contributor who can publish, or a non-owner who can change billing) is a security bug.

---

## 14. First tickets for the agent (suggested order)

1. Project skeleton: Django 6.0+ + Postgres; Django Tasks with the DB-backed worker (`db_worker`); settings for storage/CDN.
2. `actors` + `accounts`: the **unified `Actor` table** (local + remote) and `Instance` table with RSA-2048 & Ed25519 keys (FEP-521a); `User` auth split from `Actor` identity (passkeys/WebAuthn + TOTP + OAuth 2.1/OIDC); WebFinger, NodeInfo, instance actor; publish FEDERATION.md (FEP-67ff).
3. `federation` core: AS2 (de)serialization, HTTP Signatures out + verify with **cavage↔RFC 9421 double-knock** (accept both inbound), FEP-8b32 integrity proofs, inbox/outbox, Django Tasks delivery with retries, secure-mode fetch.
4. `comics`: comic actor (OneToOne → Actor), series/chapter/page models, publish + scheduling engine; `ComicRole` collaboration (owner/editor/contributor/moderator) + invites + per-page author credit.
5. `media`: upload → derivatives (responsive + federation-capped) → Bunny Storage/CDN with immutable cache headers; alt-text-required publish gate; per-comic storage accounting; uncounted remote-media cache with TTL eviction.
6. Page → `Note` serializer with attachments/alt/CW/link-back; `Create`/`Update`/`Delete` federation; **gating guard at the serializer.**
7. `social`: follows/accepts, likes, boosts, notifications, reading progress; local dogfooded as AP (`Actor → Actor`).
8. Comments: inbound replies, threading, reply-tree crawl, per-page moderation + approve-before-show.
9. `memberships`: Ko-fi + BMAC connect, webhook ingest + BMAC reconcile, email→Actor verification/linking, supporter status → gating; per-comic plan ($10/mo, 10 GB) via Stripe Billing + quota enforcement + storage add-ons; plan-lapse lifecycle (dunning → grace → freeze/read-only → archive+export); operator-configurable pricing.
10. `moderation` (Mastodon-parity, §9): user mute/block + `Block` federation, keyword filters, personal domain blocks; comic-owner commenter bans; admin account silence/suspend/freeze + domain suspend/silence/reject-media/reject-reports; unified reports queue (`Flag` in/out) with forwarding; moderation notes + audit log; allowlist toggle; report → review → takedown path.
11. Reader UX (paged nav, archive, resume), personal feed, discovery.
12. RSS/Atom, OpenGraph/oEmbed, sitemaps.
13. Interop test harness (§10): automated checks against Mastodon 4.7, a cavage-only server (GoToSocial/Akkoma), and a modern non-Mastodon server (Mitra/Fedify).
14. *(v2)* Rich interactions (§10): quote posts (FEP-e232 + FEP-044f), reply controls (FEP-5624), search-indexing consent (FEP-5feb), threading context (FEP-7888), payment links (FEP-0ea0).
