# Reader and Archive (11a) Implementation Plan

**Goal:** Serve a public, server-rendered comic home, series archive, and paged reader with HTMX navigation, keyboard/swipe, and prefetch.

**Spec:** `docs/superpowers/specs/2026-10-18-reader-ux-design.md`

## Task 1: Static/templates shell, reading order, comic home, archive

**Files:** `config/settings/base.py`, `static/…`, `templates/comics/…`, `comics/reading.py`, `comics/reader_views.py`, `comics/urls.py`, `tests/test_reader.py`.

1. Add `STATICFILES_DIRS = [BASE_DIR / "static"]`.
2. Vendor HTMX: `mkdir -p static/vendor && curl -L https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js -o static/vendor/htmx.min.js`.
3. Add `templates/comics/base.html` (loads static CSS/JS/HTMX, nav links to sign-in/out) and `comic_home.html` / `series_archive.html`.
4. `comics/reading.py`: `ordered_pages(comic)`, `series_pages(series)`, `neighbors(page)`, `latest_page(comic)` using published pages ordered by `series__position, series__id, position, id`.
5. `comics/reader_views.py`: `_comic_or_404` (404 when `accounts.publicly_visible(comic.actor)` is false), `comic_home` (series cards with first published page thumbnail via `derivative_url(media, DerivativeKind.THUMBNAIL)`), `series_archive` (published pages, `?month=YYYY-MM`, 50/page pagination), `latest` (302).
6. `comics/urls.py`: HTML routes as `/comics/<slug>/`, `/comics/<slug>/latest/`, `/comics/<slug>/<slug:series_slug>/`, `/comics/<slug>/<slug:series_slug>/<int:position>/`.
7. Tests for ordering, home, archive, month filter, 404s, suspended comic.

## Task 2: Reader view, gating, HTMX partial, JS

**Files:** `comics/reader_views.py`, `templates/comics/reader.html`, `templates/comics/_reader.html`, `templates/comics/gated.html`, `static/js/reader.js`, `static/css/reader.css`, `tests/test_reader.py`.

1. `reader(request, slug, series_slug, position)`:
   - published page only; 404 otherwise.
   - access: anonymous + tier page → redirect to `account_login?next=<path>`; signed-in without access → `gated.html` 403; supporter → 200.
   - context: page, comic, series, neighbors (first/prev/next/latest), display image URLs, alt text, transcript, author commentary, `next_image` (next page first display URL).
   - `HX-Request` → render `_reader.html`; else `reader.html` (which includes partial inside `#reader`).
2. `_reader.html`: root `<section id="reader" data-next-image="…">`; images; alt; `<details>` transcript; commentary; nav anchors with `hx-get`, `hx-target="#reader"`, `hx-select="#reader"`, `hx-swap="outerHTML"`, `hx-push-url="true"`, `data-nav`.
3. `reader.js`: ArrowLeft/Right click `[data-nav=prev|next]`; horizontal swipe; prefetch `data-next-image` on load and `htmx:afterSwap`.
4. `gated.html`: 403 upgrade copy with comic/series links.
5. Tests: full vs partial, nav order across series, latest redirect, drafts 404, anonymous redirect, non-supporter 403, supporter 200, alt/transcript/prefetch attribute.

## Task 3: Verification/polish

1. Full suite, ruff check/format, makemigrations check.
2. Commit and final review.
