# Reports, Notes, and Audit (ticket 10c) — design

Status: approved in conversation, pending review of this document.
Scope: spec §9's reports workflow — local user reports, inbound `Flag`,
scoped queue with assignment/resolution, forwarding to the offender's home
instance, account moderation notes, and an append-only audit log. Instance
account/domain actions are 10d.

## Context

`Instance.reject_reports` already short-circuits inbound `Flag`s with 202, but
there is no `Flag` handler, no report storage, and no queue. `social.moderation`
already hides/approves comments and bans commenters. Comment moderation
permission is `comics.permissions.can_moderate` (owner/editor/moderator).

## Decisions

- One `Report` model with exactly one target (`actor`, `page`, or `comment`)
  and exactly one reporter (local `User` or remote `Actor`).
- Local reports are deduped per reporter+target while open/assigned.
- Forwarding is optional at report time (`forward=true`) and retryable by a
  moderator. It emits `Flag` from the reporter's personal actor to the
  offending remote actor/comment author's instance.
- Instance staff (`is_staff`) see everything; comic moderators see only
  reports whose target belongs to their comic. Person/remote-account reports
  are staff-only.
- Inline actions in the queue are assignment, resolve/reject, forward, and
  hide/approve/ban-commenter for comment targets. Account actions wait for
  10d.
- Notes are account notes, staff-only.
- Every queue action and note writes one append-only audit row.

## Models

`moderation/models.py` (migration `0003_reports`):

`Report`:

| field | notes |
| --- | --- |
| `target_actor` / `target_page` / `target_comment` | nullable FKs; exactly one set |
| `reporter_user` / `reporter_actor` | nullable; exactly one set |
| `reason` | text |
| `category` | `spam`, `abuse`, `illegal`, `other` |
| `status` | `open`, `assigned`, `resolved`, `rejected` |
| `assigned_to` | nullable User |
| `resolution` | text, blank |
| `forwarded_at` | nullable datetime |
| `remote_activity_id` | blank; unique when non-empty |

`ModerationNote`: `author` (User), `actor` (Actor), `body`, timestamps.

`ModerationAudit`: nullable `actor` (User), `action`, `target` string,
`data` JSON, `created_at`. Append-only: no update/delete surface.

## Local reporting

`POST /moderation/reports` `{target_type, target_id, reason, category,
forward}` (login required, CSRF-protected). Validation: target exists, reason
non-empty, category in the enum (default `other`), `target_type` in
`actor|page|comment`. Dedupe: an open/assigned report by the same reporter for
the same target is updated with the new reason/category and returned.

`forward=true` is only allowed when the offending actor is remote (remote
account, or a comment whose author is remote); otherwise 400.

## Forwarding

`forward_report(report, moderator=None)`:

- account target: `object` = remote actor `ap_id`, destination = that actor's
  inbox/shared inbox;
- comment target: `object` = comment `ap_id`, destination = comment author's
  inbox/shared inbox;
- page targets and local authors are not forwardable.

The flag's `actor` is the reporting user's personal actor
(`Actor.user == user`); without one, forwarding returns 409 `no_actor` and the
report stays open for a later retry. Payload:
`{"type": "Flag", "object": <url>, "content": "<category>: <reason>"}` with a
namespaced id, delivered with `fan_out`. Success sets `forwarded_at`.

`POST /moderation/reports/<id>/forward` retries for open/assigned reports.

## Inbound Flag

`moderation/handlers.py` registers `Flag`: resolve `object` (the actor `ap_id`,
page `ap_id`, or comment `ap_id`) to a local row; store
`Report(reporter_actor=activity.actor, remote_activity_id=activity.id)` with
`content` as the reason and `other` category. Unknown targets are acknowledged
but not stored; redelivery is deduped by `remote_activity_id`.
`Instance.reject_reports` keeps its existing 202 short-circuit.

## Queue and scoping

`can_view(user, report)` / `visible_reports(user)`:

- `is_staff` → all reports;
- otherwise the target resolves to a comic the user can moderate: page/comment
  via `page.series.comic`, actor via its comic (`actor.comic`) — person/remote
  account reports are staff-only.

Endpoints (all login required):

- `GET /moderation/reports?status=…&assigned=me|unassigned|any&limit=50`
  (cap 200, newest first), `GET /moderation/reports/<id>` with resolution;
  target-actor notes are included only for staff.
- `POST /moderation/reports/<id>/assign` `{assigned_to?}` — staff-only,
  defaults to self, `open|assigned` → `assigned`.
- `POST /moderation/reports/<id>/resolve` `{status: resolved|rejected,
  resolution}` — scoped, only open/assigned, 409 when already closed.
- `POST /moderation/reports/<id>/forward` — scoped (§Forwarding).
- `POST /moderation/reports/<id>/comment-action`
  `{action: hide|approve|ban_commenter}` — comment targets only, scoped to the
  comment's comic, delegates to `social.moderation`.
- `POST /moderation/actors/<id>/notes` `{body}` and
  `GET /moderation/actors/<id>/notes` — staff-only.
- `GET /moderation/audit?target=…&limit=100` — staff-only, newest first.

Errors: 400 invalid input/non-forwardable/wrong target, 403 not permitted,
404 unknown or not visible, 409 closed/`no_actor`.

## Audit

`record_action(actor, action, *, target="", data=None)` writes one row.
Actions: `report.created`, `report.assigned`, `report.resolved`,
`report.rejected`, `report.forwarded`, `note.created`, `comment.hidden`,
`comment.approved`, `commenter.banned`. Inbound reports log `actor=None` with
the remote actor in `data`. No update or delete endpoints; not registered in
Django admin.

## Layout

- `moderation/models.py`, `moderation/migrations/0003_reports.py`
- `moderation/reports.py` (report/forward/queue/audit services)
- `moderation/handlers.py` (add `Flag`)
- `moderation/views.py`, `moderation/urls.py`
- `tests/test_moderation_reports.py`

## Testing

- Constraints and inbound dedupe; dedupe of open local reports.
- Report creation for each target type; validation; unknown targets;
  local-target forwarding rejected.
- Forwarding: emitted `Flag` shape/destination, `forwarded_at`, audit, no-actor
  409, moderator retry.
- Inbound `Flag`: each target type, dedupe, unknown ignored, reject_reports
  unchanged.
- Scoping: staff all, comic moderator scoped, outsiders 404/403, assign
  staff-only.
- Lifecycle: assign, resolve, reject, closed 409, invalid status 400.
- Inline comment actions and permissions; notes staff-only; audit rows/filters.

## Out of scope

Instance account actions (10d), reporter notifications/email, bulk actions,
unresolved remote `Flag` targets, spam heuristics, admin UI, and audit
editing/deletion.

## Success criteria

- Users can report accounts/pages/comments and optionally forward remote
  offenders.
- Inbound `Flag`s land in the same scoped queue with dedupe.
- Moderators can assign, resolve/reject, forward, and hide/approve/ban from a
  report, with one audit row per action.
- Account notes are staff-only and audited.
- Full suite passes, no migration drift, lint clean.
