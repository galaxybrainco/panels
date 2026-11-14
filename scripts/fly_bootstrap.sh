#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <staging|production>" >&2
  exit 2
fi

target="$1"
case "$target" in
  staging)
    app="panels-staging"
    db="panels-staging-db"
    ;;
  production)
    app="panels-production"
    db="panels-production-db"
    ;;
  *)
    echo "unknown environment: $target" >&2
    exit 2
    ;;
esac

region="${FLY_REGION:-iad}"
org="${FLY_ORG:-personal}"

if ! command -v flyctl >/dev/null 2>&1; then
  echo "flyctl is required: https://fly.io/docs/flyctl/install/" >&2
  exit 1
fi

DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')}"
FIELD_ENCRYPTION_KEY="${FIELD_ENCRYPTION_KEY:-$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')}"
DJANGO_EMAIL_BACKEND="${DJANGO_EMAIL_BACKEND:?set DJANGO_EMAIL_BACKEND (production mail backend)}"
DEFAULT_FROM_EMAIL="${DEFAULT_FROM_EMAIL:-noreply@${app}.fly.dev}"

echo "==> Creating app $app"
flyctl apps create "$app" --org "$org" 2>/dev/null || true

echo "==> Creating Postgres $db"
flyctl postgres create --name "$db" --region "$region" --initial-cluster-size 1 2>/dev/null || true

echo "==> Attaching $db to $app (sets DATABASE_URL)"
flyctl postgres attach "$db" --app "$app" 2>/dev/null || true

secrets=(
  "DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY"
  "FIELD_ENCRYPTION_KEY=$FIELD_ENCRYPTION_KEY"
  "DJANGO_EMAIL_BACKEND=$DJANGO_EMAIL_BACKEND"
  "DEFAULT_FROM_EMAIL=$DEFAULT_FROM_EMAIL"
)
for name in \
  MEDIA_STORAGE_BUCKET MEDIA_STORAGE_ENDPOINT_URL MEDIA_STORAGE_ACCESS_KEY \
  MEDIA_STORAGE_SECRET_KEY MEDIA_STORAGE_CUSTOM_DOMAIN MEDIA_CDN_BASE_URL \
  BILLING_ENABLED BILLING_PRICE_MONTHLY BILLING_PRICE_ANNUAL \
  BILLING_STORAGE_ADDON_PRICE STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET \
  WEBSUB_HUB_URL INSTANCE_OPEN_REGISTRATIONS; do
  if [ -n "${!name:-}" ]; then
    secrets+=("$name=${!name}")
  fi
done

echo "==> Setting secrets on $app"
flyctl secrets set --app "$app" "${secrets[@]}"

echo "Next steps:"
echo "  1. Configure the GitHub '$target' environment with FLY_API_TOKEN."
echo "  2. Deploy: flyctl deploy --config fly.${target}.toml --remote-only"
echo "  3. Set up Postgres backups: flyctl postgres backup list -a $db"
echo "  4. Add Bunny storage/CDN secrets if not provided."
