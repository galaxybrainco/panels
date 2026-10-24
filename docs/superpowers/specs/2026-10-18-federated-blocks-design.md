# Federated Blocks (ticket 10b) — design

Status: approved in conversation, pending review of this document.
Scope: user-level blocks from spec §9 — outbound `Block`/`Undo Block`, inbound
block storage and enforcement, and hiding blocked actors from notifications
and future feeds. Mutes and filters were 10a; instance/domain actions are 10d.

## Context

10a added local-only mutes/filters/domain blocks and a notification gate.
`social.services.follow`/`unfollow` already send `Follow`/`Undo` through
`federation.activitypub.build_activity` + `federation.delivery.fan_out`.
Signup creates each user's personal actor (`accounts/forms.py`), which is the
source actor for blocks.

## Decisions

- A block is a row between two `Actor`s: the user's personal actor and the
  target, local or remote. Blocking a remote target emits `Block`; blocking a
  local target is local-only.
- Unblocking a remote target emits `Undo Block`; local unblock is local-only.
- A block removes follow relationships in both directions but does not delete
  likes, boosts, or comments. Hiding is applied through shared visibility
  helpers and the notification gate.
- Inbound activities from an actor with any block involving a local actor
  (either direction) are dropped with 202, except `Block` (so reciprocal
  blocks are recorded) and `Undo` (so a remote can clear its block). Nothing
  else from that actor is stored.
- Inbound `Block` from a remote actor is stored, removes follows both ways,
  and is enforced by the same gate. Inbound `Undo Block` removes the row.
- Blocks suppress notifications in both directions through
  `notification_allowed`.

## Data model

`moderation.Block` (migration `0002_block`):

| field | notes |
| --- | --- |
| `blocker` | FK `actors.Actor`, `related_name="blocks_made"` |
| `blocked` | FK `actors.Actor`, `related_name="blocked_by"` |
| `activity_id` | outbound/inbound Block activity id, blank for local blocks |
| `created_at` | auto |

Constraints: unique `(blocker, blocked)`, check `blocker != blocked`.

## Services

`moderation/blocks.py`:

- `personal_actor(user)` → the actor whose `user` is this user, or `None`.
- `block_actor(user, target)`:
  - no personal actor → `NoActorError`; self → `SelfBlockError`.
  - `get_or_create` the row; delete follows in both directions.
  - on first creation of a remote target: build
    `{"type": "Block", "actor": personal, "object": target.ap_id}` with id
    `<personal.ap_id>#blocks/<uuid>`, fan out to the target inbox
    (`shared_inbox or inbox`), and persist the activity id.
  - idempotent repeats do not emit again.
- `unblock_actor(user, target)`:
  - missing row → `False`; no personal actor → `NoActorError`.
  - remote target: emit `Undo` whose object is the Block activity
    (`id`, `type`, `actor`, `object`), with id `<personal.ap_id>#unblocks/<uuid>`.
  - delete the row.
- `record_inbound_block(activity)` — resolve the local blocked actor from the
  activity object (string id or `{"id": ...}`), upsert the row with the
  activity id, and remove follows both directions. Unknown local actor → no-op.
- `handle_undo_block(activity, obj)` — delete the row matching the Block
  activity id and/or blocked actor; unknown → no-op.
- `has_block_with_local(actor)` — any block involving this actor and a local
  actor (either direction).
- `is_blocked(user, actor)` — any block between the user's personal actor and
  the actor, either direction.
- `hidden_actor_ids(user)` — personal actor's blocks and blockers, for feeds.
- Errors `NoActorError`, `SelfBlockError`.

## Inbound enforcement

`federation.inbound.process_inbox`, after the actor and signature checks:

- If `has_block_with_local(actor)` and the activity type is not `Undo`, return
  202 `{"accepted": true}` without storing or dispatching.
- `Block` activities are handled by a new handler registered from
  `moderation/apps.py` (`ready()`), calling `record_inbound_block`.
- `social.handlers.handle_undo` gains a `Block` branch calling
  `handle_undo_block`.

## Notification gate

`moderation.services.notification_allowed` checks `blocks.is_blocked(user,
actor)` first and suppresses in either direction; 10a's mute/thread/domain/
keyword rules follow unchanged.

## Endpoints

- `POST /moderation/blocks/<uuid:actor_id>` → `{"blocked": true}`;
  `DELETE` → `{"blocked": false}`. `login_required`, CSRF-protected.
- 404 unknown actor, 409 `no_actor` when the user has no personal actor,
  400 `invalid_target` for blocking your own actor.
- No billing-style feature flag: blocks are core safety behavior.

## Testing

- Services: follows removed both directions; remote block emits exactly one
  `Block` with the right id/actor/object and persists `activity_id`; local
  block emits nothing; repeat block is idempotent; unblock emits `Undo` for
  remote and deletes the row; no personal actor/self errors.
- Handlers: inbound `Block` stores the row and removes follows; `Undo Block`
  removes it; unknown targets no-op.
- Gate: `has_block_with_local` both directions; `should_drop_inbound` true for
  normal activities and false for `Undo`; notifications suppressed in both
  directions.
- Endpoints: block/unblock round trip, no-actor 409, self 400, unknown 404,
  anonymous 302.
- Existing moderation, social, and federation suites stay green.

## Out of scope

Reports/`Flag`/notes/audit (10c), instance and domain admin actions (10d),
feeds/UI (ticket 11), retroactive deletion of likes/boosts/comments, block
lists/UI, and per-actor (non-personal) block management.

## Success criteria

- A user with a personal actor can block a remote actor; the remote receives a
  signed `Block`, and unblocking sends `Undo Block`.
- Blocks remove follows both ways, suppress notifications both ways, and are
  exposed to feeds through `hidden_actor_ids`.
- Inbound activities from blocked actors are dropped, while reciprocal
  `Block` and `Undo Block` are still processed.
- Full suite passes, no migration drift, lint clean.
