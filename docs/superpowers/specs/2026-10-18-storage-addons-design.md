# Storage Add-ons and Operator Pricing (ticket 9c2b) — design

Status: approved in conversation, pending review of this document.
Scope: paid storage blocks above the per-comic base cap, and making the
base/add-on numbers operator config. Billing UI and further pricing models
remain out of scope.

## Context

9c1 creates a Stripe subscription per comic and 9c2a owns its lifecycle. The
plan quota is currently written once at plan creation from
`MEDIA_STORAGE_QUOTA_BYTES`, and subscription sync ignores line items. The
product spec (§7) asks for paid storage add-ons above the 10 GB base rather
than a hard wall, with price and cap as deploy config.

## Decisions

- Storage add-ons are an extra subscription item on the comic's existing
  subscription: one add-on price with `quantity = blocks`.
- Each block is `BILLING_STORAGE_ADDON_BYTES` (default 10 GiB); the price id
  comes from `BILLING_STORAGE_ADDON_PRICE`. Unset price disables add-ons.
- Quota is always `MEDIA_STORAGE_QUOTA_BYTES + units × block`, recomputed from
  Stripe on every subscription event. Stripe stays the source of truth.
- Owners buy blocks at checkout and change them later through a dedicated
  JSON endpoint; the Customer Portal is not required for this.
- A request cap (`BILLING_STORAGE_ADDON_MAX_UNITS`, default 100) is a sanity
  limit, not a billing feature.

## Data model

`ComicPlan` gains `stripe_addon_item_id` (`CharField(max_length=255,
blank=True, default="")`), the subscription item used for storage (empty when
none). Migration `billing/0004_addon_item.py`.

No other model changes: `quota_bytes` remains the single quota source read by
`media.quota`.

## Configuration

| setting | default | purpose |
| --- | --- | --- |
| `BILLING_STORAGE_ADDON_PRICE` | `""` | Stripe price id for one block; unset disables |
| `BILLING_STORAGE_ADDON_BYTES` | `10 * 1024**3` | bytes per block |
| `BILLING_STORAGE_ADDON_MAX_UNITS` | `100` | max blocks per comic per request |
| `MEDIA_STORAGE_QUOTA_BYTES` | `10 * 1024**3` | existing base cap |

## Quota math

`billing/quota.py`:

- `addon_units(subscription)` — sums `quantity` over items whose price id
  equals `BILLING_STORAGE_ADDON_PRICE`; `0` when unconfigured or absent.
- `addon_item(subscription)` — returns the first matching item's id (or `""`)
  and the summed quantity.
- `quota_bytes_for(subscription)` — base plus units × block.

`sync_subscription` sets `quota_bytes` and `stripe_addon_item_id` on both
creation and update. This means add-on changes and operator base-cap changes
both reach existing plans on their next Stripe event. Frozen/archived plans
recompute too; uploads are blocked regardless.

## Checkout

`POST /comics/<uuid>/billing/checkout` accepts optional `addon_units`
(default `0`).

- Integers only, `0 <= n <= BILLING_STORAGE_ADDON_MAX_UNITS`; otherwise 400
  `invalid_units`.
- `n > 0` with no add-on price → 409 `addons_unavailable`.
- Line items: base price × 1, plus add-on price × n when `n > 0`. Everything
  else (customer, metadata, URLs, ACTIVE-plan 409) is unchanged. The webhook
  sets the resulting quota.

## Add-on endpoint

`POST /comics/<uuid>/billing/addons`, owner-only, `BILLING_ENABLED` gated,
JSON `{"units": n}` (required).

1. Parse and validate: malformed JSON → 400 `invalid_payload`; missing,
   non-integer, boolean, negative, or above max → 400 `invalid_units`.
2. Require a plan with `stripe_subscription_id` in `ACTIVE`/`PAST_DUE`; else
   409 `no_subscription`. No add-on price → 409 `addons_unavailable`.
3. Resolve the item: use `plan.stripe_addon_item_id`; if empty, retrieve the
   subscription, find the item at the add-on price, and persist its id.
4. `n == 0`: delete the item if present, otherwise succeed without a Stripe
   call. `n > 0`: modify the existing item's quantity or create a new item.
5. Return `{"units": n}`; do not write quota locally — the next subscription
   webhook does.

Errors map to `invalid_units`/`invalid_payload` 400, `addons_unavailable`/
`no_subscription` 409, `not_owner` 403, `stripe_error` 502, and 404 when
billing is disabled.

## Layout

- `billing/quota.py` — add-on parsing and quota math.
- `billing/stripe_client.py` — `retrieve_subscription`,
  `create_subscription_item`, `modify_subscription_item`,
  `delete_subscription_item` through the existing `_run` seam.
- `billing/services.py` — `start_checkout(..., addon_units=0)`,
  `set_addon_units(comic, user, units)`; errors `InvalidUnitsError`,
  `AddonsUnavailableError`, `NoSubscriptionError`.
- `billing/views.py` / `billing/urls.py` — checkout parsing and the add-on
  view (`billing-addons`).
- `billing/webhooks.py` — quota/item sync via `billing/quota.py`.
- `billing/migrations/0004_addon_item.py`.

## Safety and idempotency

- Validate before any Stripe call; the only local write before success is
  persisting a looked-up item id.
- Endpoint effects reach the quota through the webhook (Stripe truth). Until
  then the quota is unchanged; replayed webhooks recompute the same numbers.
- A stale stored item id falls back to retrieval; the webhook refreshes it.
- Repeated identical `units` calls are harmless; the last Stripe quantity
  wins.
- Existing quantities above a lowered request cap are still honored by quota
  math; only new requests are capped.

## Testing

- Checkout: second line item with quantity; zero/absent adds none; invalid
  units; unavailable add-ons at checkout.
- Endpoint: create, modify, delete-at-zero, zero no-op, retrieval fallback,
  non-owner/disabled/no-subscription/invalid units/Stripe failure paths.
- Webhook quota: base + quantity × block stored with item id; removal restores
  base and clears the id; operator base changes apply on the next event;
  unrelated items ignored when unconfigured.
- Media: add-on quota allows an upload above the base cap and rejects one
  above base + blocks.
- All existing billing/lifecycle/media tests stay green.

## Out of scope

Multi-tier/fixed add-on menus, per-GiB pricing, proration previews,
portal-side quantity configuration, usage-based billing, refunds/disputes,
plan transfer, archive export, and billing UI.

## Success criteria

- Owners can buy blocks at checkout and change or remove them later.
- After each Stripe event, `plan.quota_bytes` equals the configured base plus
  the subscription's blocks.
- Unconfigured add-ons are cleanly unavailable with no partial changes.
- Full suite passes, no migration drift, lint clean.
