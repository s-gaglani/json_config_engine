#!/usr/bin/env bash
# setup.sh – Run database migrations and create a superuser non-interactively.
# Usage: bash setup.sh
# Required env vars: DJANGO_SUPERUSER_USERNAME, DJANGO_SUPERUSER_EMAIL, DJANGO_SUPERUSER_PASSWORD
set -euo pipefail

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.development}"

echo "==> Running database migrations..."
python manage.py migrate --no-input

echo "==> Collecting static files..."
python manage.py collectstatic --no-input --clear 2>/dev/null || true

echo "==> Creating superuser (skipped if already exists)..."
python manage.py createsuperuser \
    --no-input \
    --username "${DJANGO_SUPERUSER_USERNAME:-admin}" \
    --email "${DJANGO_SUPERUSER_EMAIL:-admin@example.com}" \
    2>/dev/null || echo "   Superuser already exists, skipping."

echo "==> Setup complete."
