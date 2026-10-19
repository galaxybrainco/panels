# Plan-Lapse Lifecycle (ticket 9c2a) — design

Status: approved in conversation, pending review of this document.
Scope: what happens when a comic's own paid plan stops paying. 9c2a adds the
dunning → grace → freeze → archive ladder, owner notices, and the upload gate.
Storage add-ons and further operator pricing configuration are 9c2b.

## Context

9c1 made Stripe the source of truth for `ComicPlan` status: paid states →
`ACTIVE`, everything else → `PAST_DUE`. `PlanStatus` already defines `FROZEN`
and `ARCHIVED`, and `media.quota.ensure_within_quota` is the single gate used
by `media.services.add_media`. Nothing acts on `PAST_DUE` yet, and a frozen
plan would still accept uploads under the default quota.

The product spec §7 requires a non-destructive ladder: dunning → grace →
frozen/read-only → archive with export. The comic must keep serving and
federating throughout; deleting federated content is explicitly forbidden.

## Decisions

- `PAST_DUE` is the grace state: the plan stays fully usable (including
  uploads) until `grace_until` passes.
- Grace starts on the first non-paid event and is a fixed window
  (`BILLING_GRACE_DAYS`, default 7). Later failures do not extend it.
- `FROZEN` blocks **new uploads only**. Editing, removing media, adding pages,
  publishing, scheduling, serving, and federation all continue.
- `ARCHIVED` is the last rung of the lapse ladder and equally
  non-destructive: same upload block, no content removal. A paid subscription
  still restores it to `ACTIVE`. Export remains out of scope.
- Stripe owns retries and receipts; we own the state machine and the notices.
- Owners get one email per real transition (past-due, frozen, restored).
  Archive is quiet.

## Data model

`ComicPlan` gains two nullable timestamps (migration
`billing/0003_plan_lifecycle.py`):

| field | purpose |
| --- | --- |
| `grace_until` | deadline for the PAST_DUE window; set once |
| `frozen_at` | when the plan was frozen; drives auto-archive |

No new status values. `ARCHIVED` keeps `frozen_at` for audit.

## Configuration

Added to `config/settings/base.py`, env-driven:

| setting | default | purpose |
| --- | --- | --- |
| `BILLING_GRACE_DAYS` | `7` | days from first failure to freeze |
| `BILLING_ARCHIVE_AFTER_DAYS` | `90` | days frozen before auto-archive |

Plan quota continues to come from `ComicPlan.quota_bytes` (set at creation by
9c1); 9c2b will derive it from subscription items.

## State machine

One function owns every transition: `billing.lifecycle.apply_status(plan,
new_status, now=None) -> bool`. It updates status/timestamps, saves, and sends
notices only on a real transition.

| from → to | effect |
| --- | --- |
| `ACTIVE` → `PAST_DUE` | `grace_until = now + BILLING_GRACE_DAYS`; email past-due |
| `PAST_DUE` → `PAST_DUE` | no-op (no reset, no email) |
| `PAST_DUE` → `FROZEN` | `frozen_at = now`; email frozen |
| `FROZEN` → `ARCHIVED` | status only (quiet) |
| any → `ACTIVE` | clear `grace_until`/`frozen_at`; email restored if previous ≠ active |
| `FROZEN`/`ARCHIVED` → `PAST_DUE` | ignored (late `unpaid` updates cannot undo a freeze) |

A plan is created by the first subscription webhook in `ACTIVE` and then
transitioned to the mapped status, so a subscription that starts `past_due`
still gets its grace window and notice.

## Webhook integration

- `customer.subscription.created|updated|deleted`: update ids and
  `current_period_end`, then call `apply_status` with the mapped status.
- New `invoice.payment_failed` (in `PAYMENT_EVENTS`): resolve the subscription
  id from the invoice (string or expanded object), find the plan by
  `stripe_subscription_id`, and call `apply_status(plan, PAST_DUE)`. Unknown
  invoices or plans are ignored with 200.
- Payment success is not a new event: Stripe's subscription update to
  `active`/`trialing` restores the plan.
- Replays and repeated failures are harmless: notices fire only when the
  status actually changes.

## Lifecycle command

`enforce_plan_lifecycle` (management command, cron-driven like
`publish_due_pages` / `expire_supporters`):

1. `PAST_DUE` with `grace_until <= now` → `FROZEN` via `apply_status` (one
   email each). Plans with a null `grace_until` are left alone until a
   webhook sets one.
2. `FROZEN` with `frozen_at <= now - BILLING_ARCHIVE_AFTER_DAYS` → `ARCHIVED`.
3. Prints frozen/archived counts for cron logs.

## Enforcement

- `media.quota.plan_blocks_uploads(comic)`: true for `FROZEN`/`ARCHIVED`.
- `ensure_within_quota` raises
  `ValidationError({"file": "This comic is read-only while its plan is frozen."})`
  for those plans (archived uses the same wording), before the byte check.
- `comic_storage_quota` honors `plan.quota_bytes` for `ACTIVE` and `PAST_DUE`,
  falling back to `MEDIA_STORAGE_QUOTA_BYTES` otherwise. This keeps a
  subscribed quota meaningful during grace and prepares 9c2b.
- Nothing in `comics` changes: frozen/archived plans do not affect editing,
  publishing, scheduling, serving, or federation.

## Notices

`billing/notifications.py` sends plain-text `send_mail` messages to the
comic's owner (`ComicRole.Role.OWNER`), naming the comic, the new state, and
the grace deadline or restore. Missing owner/email is a silent no-op. Each
send is wrapped: an email failure is logged and never breaks the transition or
the webhook response.

## Layout

- `billing/lifecycle.py` — `apply_status`, `freeze_due_plans`, `archive_old_plans`.
- `billing/notifications.py` — `notify_past_due`, `notify_frozen`, `notify_restored`.
- `billing/webhooks.py` — subscription sync delegates to `apply_status`;
  `PAYMENT_EVENTS` and invoice lookup.
- `billing/management/commands/enforce_plan_lifecycle.py`.
- `media/quota.py` — upload gate and quota-status refinement.
- `billing/migrations/0003_plan_lifecycle.py`.

## Testing

- Webhooks: `invoice.payment_failed` sets `PAST_DUE` + grace and sends one
  email; repeats do not reset or re-send; subscription `past_due` behaves the
  same; an active update restores and emails; frozen plans ignore non-active
  updates; unknown invoices/plans are 200 no-ops.
- Command: expired grace freezes and emails; unexpired and null grace stay;
  old frozen plans archive; young ones stay; summary counts.
- Uploads: ACTIVE and PAST_DUE allowed; FROZEN and ARCHIVED rejected; media
  removal still works; a published page of a frozen comic still serves.
- Emails asserted via Django's locmem `mail.outbox`.

## Out of scope

Add-on prices/quantities and item-derived quota (9c2b), configurable base
price beyond the existing price ids, archive export/deletion, invoice success
events, Stripe dunning retries, ownership-transfer reassignment, and billing
UI.

## Success criteria

- A failed payment leaves the plan usable through the configured grace, then
  freezes uploads without touching existing content or federation.
- Paying restores the plan and clears all lifecycle timestamps.
- Long-frozen plans archive automatically and remain non-destructive.
- Owners receive exactly one notice per transition.
- Full suite passes, no migration drift, lint clean.
