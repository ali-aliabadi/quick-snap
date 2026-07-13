#!/usr/bin/env sh
set -e

# Apply DB migrations and gather static files before the app starts.
python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"
