# Deploying to Fly.io

Two Fly apps: `panels-staging` and `panels-production`, each with a web and a
Django Tasks (`db_worker`) process and a Fly Postgres database.

## Prerequisites

- `flyctl` installed and `flyctl auth login` completed.
- A production email backend (for example SMTP) passed as
  `DJANGO_EMAIL_BACKEND`.
- Optional: Bunny Storage/CDN values for `MEDIA_STORAGE_*` and
  `MEDIA_CDN_BASE_URL`.

## First-time bootstrap

Run once per environment. The script creates the app and Postgres, attaches
`DATABASE_URL`, and sets required secrets. It generates `DJANGO_SECRET_KEY` and
`FIELD_ENCRYPTION_KEY` when not supplied.

```bash
export DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
export DEFAULT_FROM_EMAIL=noreply@example.com
./scripts/fly_bootstrap.sh staging
./scripts/fly_bootstrap.sh production
```

Secrets are stored in Fly; nothing sensitive is committed. Add Stripe, Bunny,
and WebSub values by exporting them before running the script (or with
`flyctl secrets set`).

## Deploying

- Staging deploys automatically on pushes to `main`.
- Production deploys from a `v*` tag or a manual **Deploy** workflow run.
- Both workflows use a GitHub `FLY_API_TOKEN` secret scoped to the matching
  GitHub Environment (`staging` / `production`). Configure required reviewers
  on the `production` environment for approval gating.

Manual deploys:

```bash
flyctl deploy --config fly.staging.toml --remote-only
flyctl deploy --config fly.production.toml --remote-only
```

Each deploy runs `migrate` and `createcachetable` as a Fly release command
before switching traffic. `/healthz` checks the database and cache; failed
checks return 503 and keep the app unhealthy.

## Operations

```bash
flyctl status -a panels-production
flyctl logs -a panels-production
flyctl postgres backup list -a panels-production-db
flyctl ssh console -a panels-production     # run manage.py commands
flyctl scale show -a panels-production
```

Both apps run a worker process for Django Tasks. Scaling the web process does
not scale the worker; use `flyctl scale count worker=1`.

## Custom domains

The configs start on `*.fly.dev`. Add a custom domain with:

```bash
flyctl certs add panels.example.com -a panels-production
```

Then update `INSTANCE_URL`, `DJANGO_ALLOWED_HOSTS`, and
`DJANGO_CSRF_TRUSTED_ORIGINS` in `fly.production.toml` (or via
`flyctl secrets set`) and redeploy.

## Notes

- `primary_region = "iad"` in both configs; change it before first deploy if
  you need a different region.
- Production keeps one web machine warm; staging auto-stops idle web machines.
- Storage uses `MEDIA_STORAGE_*` (Bunny S3) and optional CDN settings.
