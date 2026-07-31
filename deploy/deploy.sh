#!/usr/bin/env bash
# Deploy script run ON the VPS (by you, or by GitHub Actions over SSH).
# Pulls the latest main and rebuilds/restarts the Docker Compose stack.
# entrypoint.sh already runs migrate + collectstatic on container boot.
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/quicksnap}"

# Support both Compose v2 (`docker compose`) and v1 (`docker-compose`).
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose -f docker-compose.prod.yml"
else
    COMPOSE="docker-compose -f docker-compose.prod.yml"
fi

cd "$APP_DIR"

echo "==> Fetching latest master"
git fetch --prune origin
git reset --hard origin/master

# --- Monitoring prerequisites -------------------------------------------------
# Both secrets are generated on first deploy rather than required up front: the
# app half of this deploy must not be held hostage to a manual step, or a push
# that fixes a guest-facing bug would sit undeployed waiting for a password.
#
# Prometheus authenticates to the app's token-gated /metrics using a *file*,
# because it cannot expand env vars in its own config.
# Prometheus authenticates to the app's token-gated /metrics using a *file*,
# because it cannot expand env vars in its own config.
#
# Note the `.\+` in these greps: .env ships with an empty `METRICS_TOKEN=`
# placeholder, and a plain `^METRICS_TOKEN=` would match that, so we'd think a
# secret existed and hand Prometheus an empty credential.
if ! grep -q '^METRICS_TOKEN=.\+' .env 2>/dev/null; then
    echo "==> Generating METRICS_TOKEN into .env (first run)"
    token="$(head -c 32 /dev/urandom | base64 | tr -d '=+/' | cut -c1-40)"
    # Delete the empty placeholder *and* rewrite the file, because
    # django-environ reads the FIRST match for a key: leaving a blank
    # `METRICS_TOKEN=` above the real one makes the app load the empty value
    # and 404 the endpoint while Prometheus holds a perfectly valid token.
    grep -v '^METRICS_TOKEN=' .env > .env.tmp 2>/dev/null || true
    printf 'METRICS_TOKEN=%s\n' "$token" >> .env.tmp
    mv .env.tmp .env
fi
# Materialise the token file Prometheus mounts (0600 — it is a credential).
# Select the *populated* line, in case an empty placeholder is still present.
umask 077
grep '^METRICS_TOKEN=.\+' .env | tail -1 | cut -d= -f2- \
    | tr -d '\r\n' > deploy/monitoring/.metrics_token
chmod 600 deploy/monitoring/.metrics_token
# A zero-length token would make every scrape 404 while looking configured.
if [ ! -s deploy/monitoring/.metrics_token ]; then
    echo "!! Failed to write a non-empty METRICS_TOKEN file." >&2
    echo "!! Check the METRICS_TOKEN line in $APP_DIR/.env" >&2
    exit 1
fi

# Grafana is published at /grafana/, so it must never boot on a default
# password. Generate a strong one on first deploy and print it once — this is
# the only time it is shown, so grab it from the deploy log.
GRAFANA_PW_GENERATED=""
if ! grep -q '^GRAFANA_ADMIN_PASSWORD=.\+' .env 2>/dev/null; then
    GRAFANA_PW_GENERATED="$(head -c 24 /dev/urandom | base64 | tr -d '=+/' | cut -c1-24)"
    grep -v '^GRAFANA_ADMIN_PASSWORD=' .env > .env.tmp 2>/dev/null || true
    printf 'GRAFANA_ADMIN_PASSWORD=%s\n' "$GRAFANA_PW_GENERATED" >> .env.tmp
    mv .env.tmp .env
fi
# Default the remaining Grafana vars so compose interpolation can't fail the
# whole deploy (which would also stop `web`) over a missing optional value.
grep -q '^GRAFANA_ADMIN_USER=' .env 2>/dev/null || echo 'GRAFANA_ADMIN_USER=admin' >> .env
grep -q '^GRAFANA_DOMAIN=' .env 2>/dev/null || echo 'GRAFANA_DOMAIN=alialiabadi.ir' >> .env

echo "==> Building and restarting stack"
$COMPOSE up -d --build

echo "==> Pruning old images"
docker image prune -f

echo "==> Done. Current containers:"
$COMPOSE ps

if [ -n "$GRAFANA_PW_GENERATED" ]; then
    echo
    echo "============================================================"
    echo " Grafana admin password (generated, shown ONCE):"
    echo "   user:     ${GRAFANA_ADMIN_USER:-admin}"
    echo "   password: $GRAFANA_PW_GENERATED"
    echo " Stored in $APP_DIR/.env — change it after first sign-in."
    echo " URL: https://$(grep '^GRAFANA_DOMAIN=' .env | cut -d= -f2-)/grafana/"
    echo "============================================================"
fi
