# Stripe Billing (ticket 9c1) — design

Status: approved in conversation, pending review of this document.
Scope: the platform's one first-party payment — a per-comic plan via Stripe
Billing. 9c1 covers checkout, billing portal, signed webhooks, and plan sync.
The plan-lapse ladder, storage add-ons, and operator-configurable pricing are
ticket 9c2.

## Context

`billing.ComicPlan` already models a comic's paid plan (`status`,
`quota_bytes`, `stripe_customer_id`, `stripe_subscription_id`,
`current_period_end`) and `media.quota.comic_storage_quota` already prefers an
ACTIVE plan's quota over the `MEDIA_STORAGE_QUOTA_BYTES` default. Nothing
creates or updates plans yet: this ticket makes Stripe the source of truth for
them.

The spec fixes the product shape: flat per-comic price, paid by the comic's
owner (`ComicRole.Role.OWNER`), one Stripe customer per owner so N comics are
N subscriptions under one customer. Pricing/quota stay deploy config; billing
can be off entirely for self-hosting. That instance's readers can still visit
supporters' comics because quota is the only thing billing gates.

## Decisions

- Use the official `stripe` Python SDK (resolved and pinned by `uv add`). It
  uses `requests` internally, so tests keep using `responses`.
- One Stripe customer per owner: new `billing.BillingCustomer` (OneToOne to
  `accounts.User`).
- Owner-facing JSON endpoints for checkout and the Customer Portal. No
  templates.
- Monthly and annual prices, each an operator-set Stripe price id. Annual is
  optional (unset → `interval=year` rejected).
- 9c1 reflects the minimal status mapping: paid states → `ACTIVE`, everything
  else → `PAST_DUE`. 9c2 replaces this with dunning → grace → freeze.
- Billing disabled means no billing surface at all (404) and no Stripe calls.

## Data model

`billing.BillingCustomer` (`UUIDModel`):

| field | type | notes |
| --- | --- | --- |
| `user` | OneToOne → `accounts.User` | `related_name="billing_customer"` |
| `stripe_customer_id` | `CharField(max_length=255, unique=True)` | created lazily at checkout |
| `created_at` | `DateTimeField(auto_now_add=True)` | |

`ComicPlan` is unchanged. A plan row is created on the first subscription
webhook; its `quota_bytes` comes from `MEDIA_STORAGE_QUOTA_BYTES` at creation
and is never rewritten afterwards (so 9c2 add-ons survive renewals).

## Configuration

Added to `config/settings/base.py`, all env-driven:

| setting | default | purpose |
| --- | --- | --- |
| `BILLING_ENABLED` | `False` | master switch; off → billing URLs 404, no Stripe calls |
| `STRIPE_SECRET_KEY` | `""` | Stripe API key |
| `STRIPE_WEBHOOK_SECRET` | `""` | endpoint signing secret |
| `BILLING_PRICE_MONTHLY` | `""` | required for monthly checkout |
| `BILLING_PRICE_ANNUAL` | `""` | optional; empty rejects `interval=year` |
| `BILLING_SUCCESS_URL` | `INSTANCE_URL + "/"` | Checkout success redirect |
| `BILLING_CANCEL_URL` | `INSTANCE_URL + "/"` | Checkout cancel redirect |
| `BILLING_PORTAL_RETURN_URL` | `INSTANCE_URL + "/"` | Portal return redirect |

The plan quota reuses `MEDIA_STORAGE_QUOTA_BYTES` (10 GiB): one knob for the
"$10 / 10 GB" pairing. There is no hardcoded price or cap.

`BILLING_ENABLED=True` with a missing secret key, webhook secret, or monthly
price id is a deployment error; endpoints fail closed (503 is out of scope —
they behave as Stripe-error paths, see below).

## Endpoints

Both are CSRF-protected JSON POSTs, `login_required`, and return 404 when
`BILLING_ENABLED` is false.

### `POST /comics/<uuid:comic_id>/billing/checkout`

Body (optional): `{"interval": "month"}` (default) or `"year"`.

1. Load the comic; 404 if missing. Require `ComicRole.Role.OWNER` for the
   requesting user, else 403.
2. Reject an `ACTIVE` plan with 409 `{"error": "already_subscribed"}`.
   `PAST_DUE` plans may resubscribe.
3. Validate the interval: anything but `month`/`year` → 400
   `{"error": "invalid_interval"}`; `year` with no `BILLING_PRICE_ANNUAL` → 400.
4. Get-or-create the owner's `BillingCustomer`; if it has no Stripe id,
   create one via `stripe.Customer.create(email=..., metadata={"user_id": ...})`
   and persist the id.
5. Create a `checkout.Session` with `mode="subscription"`, the owner's
   customer, one line item at the chosen price, the configured success/cancel
   URLs, `client_reference_id=comic.id`, and
   `subscription_data.metadata={"comic_id": ..., "user_id": ...}`. The
   subscription metadata is what later webhook events use to find the comic.
6. Return `{"checkout_url": session.url}`.

### `POST /comics/<uuid:comic_id>/billing/portal`

Owner-only, same 404/403 rules. Requires the owner's `BillingCustomer` with a
Stripe id; otherwise 409 `{"error": "no_billing_account"}`. Creates a
`billing_portal.Session` with `return_url=BILLING_PORTAL_RETURN_URL` and
returns `{"portal_url": ...}`.

## Webhook

`POST /billing/webhook` — CSRF-exempt and unauthenticated; 404 when billing is
disabled.

1. Verify `request.body` against the `Stripe-Signature` header and
   `STRIPE_WEBHOOK_SECRET` with `stripe.Webhook.construct_event` (SDK enforces
   the HMAC and timestamp tolerance). Invalid signature or malformed payload →
   400; nothing is written.
2. Handle `customer.subscription.created`, `.updated`, and `.deleted`. Every
   other event returns 200 `{"received": true}` and is ignored.
3. Resolve the comic from `subscription.metadata["comic_id"]`. Missing,
   unparseable, or unknown comic → 200 ignored (a 4xx would make Stripe retry
   a permanently bad event).
4. Map status: `active`/`trialing` → `ACTIVE`; `past_due`, `unpaid`,
   `incomplete`, `incomplete_expired`, `paused`, and `canceled` → `PAST_DUE`.
   A deletion also results in `PAST_DUE` (non-destructive: the comic keeps
   serving and federating; its quota falls back to the default).
5. Get-or-create `ComicPlan` for the comic. Always set `status`,
   `stripe_customer_id`, `stripe_subscription_id`, and `current_period_end`
   (unix → aware UTC; only overwrite when the payload has a value). Set
   `quota_bytes` to `MEDIA_STORAGE_QUOTA_BYTES` only when creating.
6. Return 200 `{"received": true}`. Processing is idempotent: the last event
   wins and re-delivery writes identical state.

## Code layout

- `billing/stripe_client.py` — sole importer of `stripe`. Sets
  `stripe.api_key` per call from settings and exposes `create_customer`,
  `create_checkout_session`, `create_portal_session`, `construct_event`.
- `billing/services.py` — `get_or_create_customer`, `start_checkout`,
  `open_portal`; raises typed errors `BillingDisabled`, `NotOwner`,
  `AlreadySubscribed`, `InvalidInterval`, `NoBillingAccount`, `StripeError`
  that views translate to 404/403/409/400/502.
- `billing/webhooks.py` — `handle_subscription_event(subscription)` with the
  status mapping and plan sync; unit-testable without HTTP.
- `billing/views.py`, `billing/urls.py` — thin JSON views; `config/urls.py`
  includes `billing.urls`.
- `billing/migrations/0002_billingcustomer.py`.

## Errors and security

- Invalid Stripe signature or malformed webhook body → 400, no writes.
- Stripe API failures → 502 `{"error": "stripe_error"}` with a generic body;
  the exception is logged server-side.
- Secrets, API keys, and Stripe error payloads are never logged or returned.
- Billing disabled → 404 on every billing URL; no Stripe client is touched.
- Clients must send a CSRF token on checkout/portal POSTs (normal Django
  behavior).

## Testing

`tests/test_billing.py`, using `responses` to intercept the SDK's HTTP calls
and `override_settings` for configuration:

- Checkout: returns a URL; customer is created once and reused; non-owner 403;
  unknown comic 404; billing disabled 404; bad/unsupported interval 400; active
  plan 409; Stripe error 502 with no plan writes.
- Portal: returns a URL; missing customer 409; billing disabled 404; non-owner
  403.
- Webhook: signed `created`/`updated`/`deleted` events create/update the plan;
  active/trialing/past_due mapping; invalid signature 400 with no writes;
  unknown event, unknown comic, and missing metadata → 200 with no writes;
  existing `quota_bytes` preserved on update; unix period end parsed to aware
  UTC; re-delivery is idempotent.
- Signature headers are generated with `hmac`/`time` and the test secret, not
  SDK internals.

## Out of scope (ticket 9c2 and later)

Dunning, grace windows, frozen/read-only, archive + export, invoice and
payment-failure events, storage add-ons, operator-configurable price/cap
beyond the settings above, ownership-transfer reassignment of the payer,
per-connection webhook throttling, and any billing UI beyond the JSON
endpoints.

## Success criteria

- An owner can POST checkout and receive a Stripe Checkout URL for the
  configured monthly or annual price.
- A signed subscription webhook (created/updated/deleted) leaves the comic's
  `ComicPlan` matching Stripe, with the right `PlanStatus`.
- Billing disabled leaves the app fully functional on the default quota and
  exposes no billing URLs.
- The full suite passes; no migration drift; lint clean.
