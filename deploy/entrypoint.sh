#!/usr/bin/env sh
set -e

# Prometheus multiprocess metrics: each gunicorn worker writes counter files
# here. Files from a previous container's workers are stale — those workers are
# gone but their counters would still be summed into every scrape — so start
# from an empty directory on every boot.
if [ -n "$PROMETHEUS_MULTIPROC_DIR" ]; then
    rm -rf "$PROMETHEUS_MULTIPROC_DIR"
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
fi

# Apply DB migrations and gather static files before the app starts.
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Table backing the failed-join throttle's cache. Idempotent, so it is safe on
# every boot; without it the first throttled request would 500.
python manage.py createcachetable

exec "$@"
