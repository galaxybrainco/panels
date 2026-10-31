# Instance & Domain Moderation (ticket 10d) — design

Status: approved in conversation, pending review of this document.
Scope: the instance/admin tier of spec §9 — account actions (limit, suspend,
freeze, disable, warn, force-sensitive), the domain suite (suspend, silence,
reject media, reject reports, allowlist), registration email/IP blocks, and
the cross-cutting enforcement gate. Hard account deletion and feeds stay out.

## Context

10a–10c added self-service preferences, federated blocks, reports/notes/audit.
`Actor` and `Instance` are unified rows; `Instance` already has `blocked`,
`silenced`, `allowlisted`, `reject_media`, and `reject_reports`. Inbound
already enforces blocked domains, rejected media/reports, and blocks. Signup
creates the user's personal actor and uses allauth's signup form/view.

## Decisions

- Account state is one `AccountModeration` row per actor (state + force flag +
  reason + actor/staff who changed it); history is the 10c audit log.
- `limit`/`suspend` apply to local and remote actors; `freeze`/`disable`/`warn`
  are local-only (actor has a user).
- `freeze` allows login but blocks publishing; `disable` deactivates the user;
  `restore` returns to normal and reactivates a disabled user.
- Force-sensitive marks pages the user authored/published as sensitive and
  makes future publishes sensitive before the existing CW gate.
- Domain actions are flags on `Instance` (suspend=blocked, silence, reject
  media, reject reports, allowlist); `restore` clears them all.
- Allowlist mode is deploy config (`FEDERATION_ALLOWLIST_ENABLED`, default
  false) and gates inbound/fetch for non-allowlisted remote domains.
- Registration blocks are DB rows managed by staff; IP matching uses
  `REMOTE_ADDR` only.
- Everything here is instance policy; nothing is federated as an activity.

## Account moderation

`moderation.AccountModeration` (migration `0004_account_moderation`):

| field | notes |
| --- | --- |
| `actor` | OneToOne `actors.Actor`, `related_name="moderation"` |
| `state` | `normal`, `limited`, `suspended`, `frozen`, `disabled` |
| `force_sensitive` | bool, default false |
| `reason` | text, required by actions |
| `changed_by` | nullable User |

`moderation/accounts.py`:

- `account_moderation(actor)` (get-or-create normal), `account_state`,
  `is_limited`, `is_suspended`, `is_frozen`, `is_disabled`,
  `is_force_sensitive(user)`, `can_post(user)` (active, not
  frozen/disabled/suspended), `publicly_visible(actor)` (not
  limited/suspended and domain not blocked/silenced).
- `apply_account_action(actor, staff, action, *, reason, enabled=None)`:
  - `limit`, `suspend` — any actor.
  - `freeze`, `disable`, `warn` — local actor with a user; remote → 409.
  - `disable` sets `user.is_active=False`; `restore` sets state normal and
    reactivates a disabled user; `force_sensitive` toggles the flag and marks
    `Page` rows where `author` or `published_by` is the user as sensitive.
  - `warn` sends a best-effort email with the reason; no state change.
  - Non-empty reason required for every action; each writes one
    `ModerationAudit` row (`account.limited/suspended/frozen/disabled/
    restored/warned/force_sensitive`).

Endpoints (staff-only): `GET /moderation/accounts/<actor_id>` and
`POST /moderation/accounts/<actor_id>/action` `{action, reason, enabled?}`.

## Enforcement

- Inbound (`federation.inbound.process_inbox`), after actor/block checks:
  suspended sender → 202 drop; when allowlist mode is on, remote senders from
  non-allowlisted domains → 202 drop; blocked domains keep 403; silenced
  domains are accepted.
- Fetch (`federation.remotes.fetch_remote_actor`): deny blocked domains and,
  in allowlist mode, non-allowlisted domains.
- Outbound (`federation.delivery.fan_out`): skip inboxes whose domain is a
  blocked Instance; follower fan-out excludes suspended/disabled followers;
  following a suspended actor is rejected.
- Public AP: `comics.views.page_detail` 404s when the comic actor is
  limited/suspended or its domain blocked/silenced; `social.views.comment_detail`
  404s for limited/suspended comment authors or blocked/silenced domains;
  actor outbox/followers/following 404 for limited/suspended actors.
- Frozen users: `comics.publishing.publish_page` raises `PermissionDenied`.
- Force-sensitive users: publishing marks the page `sensitive=True` before the
  existing CW gate.
- Helpers above are exported for ticket 11 feeds.

## Domain suite

`moderation/domains.py`, staff-only endpoints:

- `POST /moderation/domains/<str:domain>/action` `{action}`:
  `suspend` (blocked), `silence`, `reject_media`, `reject_reports`,
  `allowlist`, `restore` (all false).
- `GET /moderation/domains/<str:domain>` → policy.
- Domains are normalized like 10a (hostname/IDN→ASCII, ports dropped, local
  instance rejected); `Instance` rows are created on demand. Every change
  audits `domain.<action>` with `target="domain:<host>"`.
- `FEDERATION_ALLOWLIST_ENABLED` (env bool, default false) activates allowlist
  gating.

## Registration blocks

Models `BlockedEmailDomain(domain unique)` and `BlockedIP(address unique, IP
or CIDR)`, validated with `ipaddress`.

Staff endpoints (JSON):

- `GET/POST/DELETE /moderation/registration-blocks/email-domains`
  (`DELETE` body `{domain}`).
- `GET/POST/DELETE /moderation/registration-blocks/ip-blocks`
  (`DELETE` body `{address}`).
- Adds/removes audit `registration.email_domain_added/removed` and
  `registration.ip_added/removed`.

Signup: `SignupForm.clean_email` rejects exact blocked email domains;
`SignupView.form_valid` rejects a `REMOTE_ADDR` inside a blocked network with
a form error.

## Layout

- `moderation/models.py`, migration `0004_account_moderation.py`
- `moderation/accounts.py`, `moderation/domains.py`, `moderation/registration.py`
- `moderation/views.py`, `moderation/urls.py`
- `social/services.py`, `federation/delivery.py`, `federation/remotes.py`,
  `federation/inbound.py`, `comics/views.py`, `social/views.py`,
  `comics/publishing.py`, `federation/views.py`
- `accounts/forms.py`, `accounts/views.py`

## Testing

- Account actions/state/audit/warn email; local-only rejections; restore;
  disable/reactivate; force-sensitive marking; staff-only endpoints.
- Enforcement: suspended inbound drop, allowlist mode, public 404s for
  limited/suspended/silenced, outbound blocked-domain skip, suspended follower
  exclusion, following suspended rejected, frozen publish denied,
  force-sensitive publish marks sensitive.
- Domain endpoints: create/patch/restore/local rejection/audit/non-staff.
- Registration: validation, CRUD, signup email/IP rejection, audit rows.
- Existing moderation/federation/social/auth suites stay green.

## Out of scope

Hard account deletion, notification/UI surfaces, feeds/timelines (ticket 11),
AP actor `Delete` propagation, time-limited auto-expiry, Django admin UI, and
trusting proxy headers for IP blocks.

## Success criteria

- Staff can apply and reverse account/domain actions, with audit rows and
  owner/user notices where defined.
- Suspended accounts/domains are cut off from federation and public AP content;
  silenced/limited content is hidden publicly; frozen users cannot publish.
- Allowlist mode and registration blocks are enforced with clear errors.
- Full suite passes, no migration drift, lint clean.
