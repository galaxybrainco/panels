# Reader and Archive (ticket 11a) — design

Status: approved in conversation, pending review of this document.
Scope: the server-rendered reading surface — comic home, series archive, paged
reader with keyboard/swipe/preload, alt text and transcripts. Resume, personal
feed, and discovery are 11b–11d.

## Context

Comics currently expose only ActivityPub JSON (`/pages/<uuid>`) and the
allauth templates. `Page` rows are ordered by `position` inside a `Series`,
series by `position` inside a `Comic`; published pages carry `published_at`,
`ap_id`, `author_commentary`, `transcript`, and `media` rows with alt text and
derivative URLs (`media.derivative_urls.derivative_url` with `thumbnail` /
`display`). Member gating lives in `memberships.services.page_requires_tier` /
`can_view_page`, and instance moderation exposes `accounts.publicly_visible`.

## Decisions

- Django templates plus **HTMX** (vendored locally, no CDN) and a small
  vanilla-JS shim for ←/→, swipe, and next-image prefetch.
- URLs: `/comics/<slug>/`, `/comics/<slug>/<series_slug>/`,
  `/comics/<slug>/<series_slug>/<position>/`, `/comics/<slug>/latest/`.
- Reading order is series order (position, id), then the next series in comic
  order; first/prev/next/latest follow that full order.
- Only `PUBLISHED` pages are navigable or archived.
- Members/tier pages: anonymous → login redirect with `next`; signed-in
  without access → 403 upgrade page. Public/unlisted/followers-only/local-only
  pages read normally on-site.
- Suspended/limited comic actors (10d) make the comic surface 404.
- Alt text is always rendered; transcript is an expandable `<details>` block.

## Routes and views

`comics/reader_views.py` (HTML), included from `comics/urls.py`:

- `comic_home(request, slug)` — comic header, series cards (cover = first
  published page's first media thumbnail, published page count), latest link.
- `series_archive(request, slug, series_slug)` — published pages in order,
  thumbnails, titles/dates, optional `?month=YYYY-MM` date jump, `?page=N`
  pagination (50 per page).
- `reader(request, slug, series_slug, position)` — one page with all display
  images, alt text, page title, series/comic links, transcript, author
  commentary, and navigation to first/prev/next/latest. `HX-Request` renders
  the `comics/_reader.html` partial only; otherwise the full page.
- `latest(request, slug)` — 302 to the last page in reading order (404 when
  none).

Reading-order helpers live in `comics/reading.py`:
`ordered_pages(comic)` (published, series then page position), `neighbors(page)`
→ `(first, previous, next, latest)`, `latest_page(comic)`, `series_pages`.

Access helper `comics/reader_views.can_read(request, page)`:
published check → `page_requires_tier` → if gated and anonymous redirect to
login; if signed in, resolve the user's personal actor and check
`can_view_page`; failure renders the 403 upgrade template. Suspended comic
actors 404 via `accounts.publicly_visible`.

## Templates and static

- `templates/comics/base.html` — minimal document shell with navigation and
  static includes.
- `templates/comics/comic_home.html`, `series_archive.html`, `reader.html`,
  `_reader.html` (partial), `gated.html` (upgrade/403).
- `static/vendor/htmx.min.js` (pin htmx 2.0.4 at vendoring time),
  `static/js/reader.js`, `static/css/reader.css`; add
  `STATICFILES_DIRS = [BASE_DIR / "static"]`.
- Reader navigation links carry `hx-get`, `hx-target="#reader"`,
  `hx-select="#reader"`, `hx-swap="outerHTML"`, `hx-push-url="true"` and
  ordinary `href` fallbacks. `reader.js` maps ←/→ to the prev/next links,
  handles horizontal swipe, and prefetches the image from
  `#reader[data-next-image]`.
- The partial includes `data-next-image` (next page's first display
  derivative) so prefetch works after HTMX swaps.

## Testing

- Comic home: series order, cover thumbnails, published counts, latest link,
  suspended comic 404.
- Archive: published-only pages, ordering, thumbnails, month filter,
  pagination, unknown series/404.
- Reader: series-then-next-series first/prev/next, latest redirect, drafts and
  scheduled pages 404, unknown position 404.
- Gating: anonymous redirect with `next`; signed-in non-supporter 403 upgrade
  page; supporter 200.
- HTMX partial: `HX-Request` returns the fragment without `<html>`; full page
  includes the shell and static assets.
- Alt text and transcript render; next-image attribute present.
- Existing comics/federation/membership suites stay green.

## Out of scope

Resume/continue (11b), personal feed and notifications page (11c), discovery
and maturity filtering (11d), creator dashboard, infinite vertical mode,
webtoon mode, and client-side routing.

## Success criteria

- A visitor can browse a comic and its series and read published pages in
  order with keyboard/swipe navigation and prefetch.
- Member pages follow the login/upgrade rules; drafts never leak.
- Archives expose thumbnails and date jumps.
- Full suite passes, no migration drift, lint clean.
