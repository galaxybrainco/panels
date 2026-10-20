# Self-Service Moderation (ticket 10a) — design

Status: approved in conversation, pending review of this document.
Scope: the user-level safety tools from spec §9 — mutes, thread mutes,
keyword filters, personal domain blocks, and removing a follower. Everything
here is local-only and emits no ActivityPub activity.

## Context

The `moderation` app is an empty skeleton. Notifications are created by
`social.notifications.notify`, and reader feeds arrive with ticket 11. This
ticket stores per-user preferences, applies them at notification creation, and
exposes the same checks as helpers for feeds.

Every identity is one `Actor` row and every remote host is an `Instance`
domain, so these preferences are a single lookup with no local/remote branch.

## Decisions

- Mutes are local-only. They hide the actor from future feeds; the optional
  `hide_notifications` flag also suppresses that actor's notifications (default
  false, matching "option to also mute their notifications").
- Thread mutes suppress every notification tied to that page.
- Personal domain blocks hide/suppress everything from one remote domain for
  that user; the local instance domain cannot be blocked.
- Keyword filters are case-insensitive substring matches with optional expiry.
- Expiry is evaluated in queries (`expires_at IS NULL OR expires_at > now`), so
  no cleanup job is needed.
- Removing a follower deletes the local `Follow` only: no block, no activity.
- Muting yourself or an actor belonging to a comic you own is rejected.

## Models

`moderation/models.py` (migration `0001_initial`):

| model | fields | constraint |
| --- | --- | --- |
| `Mute` | `user`, `actor`, `hide_notifications` (bool, false), `expires_at` (nullable), `created_at` | unique `(user, actor)` |
| `ThreadMute` | `user`, `page`, `created_at` | unique `(user, page)` |
| `KeywordFilter` | `user`, `phrase` (255), `expires_at` (nullable), `created_at` | unique `(user, phrase)` |
| `DomainBlock` | `user`, `domain` (255), `created_at` | unique `(user, domain)` |

## Services

`moderation/services.py`:

- Read helpers (also for ticket 11 feeds): `active_mute(user, actor)`,
  `is_muted`, `is_thread_muted`, `is_domain_blocked`,
  `matching_filter(user, *, actor=None, page=None, comment=None)`,
  `is_filtered`, and the composite
  `notification_allowed(user, *, actor=None, page=None, comment=None)`.
- Writes: `mute_actor(user, actor, *, hide_notifications=False,
  expires_at=None)` (idempotent; omitted expiry means indefinite),
  `unmute_actor`, `mute_thread`/`unmute_thread`, `add_filter`/`remove_filter`,
  `block_domain`/`unblock_domain`, `remove_follower(user, actor, follow)`.
- `can_manage_actor(user, actor)` is true for the user's own actor or an actor
  of a comic they own. `remove_follower` verifies `follow.target` is the given
  actor before deleting it.
- Typed errors: `InvalidExpiryError`, `InvalidPhraseError`,
  `InvalidDomainError`, `SelfMuteError`, `NotActorOwnerError`.

## Notification gate

`social.notifications.notify` calls `notification_allowed` after its existing
self-notification check and creates nothing when it returns false. The
composite rule suppresses when any of these hold:

1. the source actor has an active mute with `hide_notifications`;
2. the page has an active thread mute;
3. the source actor's domain has an active personal domain block;
4. an active keyword filter matches the comment body, the page/series/comic
   title, or the actor's name/handle (case-insensitive substring).

Comment storage, federation, and public serving are unchanged; this is a
user-side view preference only.

## Endpoints

`moderation/urls.py`, included from `config/urls.py`; all `login_required`,
CSRF-protected JSON:

- `POST /moderation/mutes/<uuid:actor_id>` — optional
  `{"hide_notifications": bool, "expires_at": ISO-8601}`; returns
  `{"muted": true, "hide_notifications": ..., "expires_at": ...}`.
  `DELETE` returns `{"muted": false}`.
- `POST /moderation/thread-mutes/<uuid:page_id>` / `DELETE` —
  `{"muted": true|false}`.
- `GET /moderation/filters` — active filters (`id`, `phrase`, `expires_at`);
  `POST` `{"phrase", "expires_at"?}` (idempotent per phrase);
  `DELETE /moderation/filters/<uuid:filter_id>` → `{"removed": true}`.
- `POST /moderation/domain-blocks/<str:domain>` / `DELETE` —
  `{"blocked": true|false}`.
- `DELETE /moderation/actors/<uuid:actor_id>/followers/<uuid:follow_id>` —
  `{"removed": true}`.

Validation and errors:

- Unknown actor/page/filter/follow → 404; non-own filter → 404; actor not
  managed by the user for remove-follower → 403.
- Muting self or an actor of an owned comic → 400 `invalid_target`.
- Malformed JSON or non-boolean `hide_notifications` → 400 `invalid_payload`.
- `expires_at` must parse as timezone-aware ISO-8601 and be in the future →
  400 `invalid_expiry`.
- Phrase trimmed, non-empty, at most 255 characters → 400 `invalid_phrase`.
- Domain lowercased, hostname-shaped (labels separated by dots), not the local
  instance domain → 400 `invalid_domain`.
- Wrong HTTP method → 405.

## Layout

- `moderation/models.py`, `moderation/migrations/0001_initial.py`
- `moderation/services.py`
- `moderation/views.py`, `moderation/urls.py`; `config/urls.py` include
- `social/notifications.py` gate call
- `tests/test_moderation.py`

## Testing

- Mutes: defaults/toggle/expiry, idempotent update, self-mute, unknown actor,
  expired inactivity, delete.
- Thread mutes: mute/unmute, active/expired, unknown page.
- Filters: create/list/delete, duplicate phrase, expiry, validation; matching
  against comment text, page/series title, and actor name/handle,
  case-insensitively.
- Domain blocks: create/delete, malformed/local rejection, domain matching.
- Remove follower: managed actor succeeds; non-manager 403; mismatched
  follower/target 404.
- Notification gate: `hide_notifications` suppresses, plain mute does not;
  thread mute, domain block, and matching filter suppress; expired preferences
  do not; self-notifications still skipped.
- Local-only: no `Activity`/delivery rows are created by 10a actions.
- Existing social, comment, and federation tests stay green.

## Out of scope

Federated `Block`/`Undo Block` and inbound block enforcement (10b), reports and
`Flag`, moderation notes, audit log (10c), instance/domain admin actions (10d),
feeds and their UI (ticket 11), whole-word/scoped filters, mute cleanup jobs,
and notification list UI.

## Success criteria

- Users can mute accounts (optionally notifications, optionally until a time),
  mute threads, add expiring keyword filters, block remote domains
  personally, and remove followers from actors they control.
- Suppressed notifications are never created; everything else keeps working.
- No ActivityPub activity is emitted by any 10a action.
- Full suite passes, no migration drift, lint clean.
