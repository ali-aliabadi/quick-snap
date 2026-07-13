#!/usr/bin/env bash
# Deploy script run ON the VPS (by you, or by GitHub Actions over SSH).
# Pulls the latest main and rebuilds/restarts the Docker Compose stack.
# entrypoint.sh already runs migrate + collectstatic on container boot.
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/quicksnap}"
COMPOSE="docker compose -f docker-compose.prod.yml"

cd "$APP_DIR"

echo "==> Fetching latest master"
git fetch --prune origin
git reset --hard origin/master

echo "==> Building and restarting stack"
$COMPOSE up -d --build

echo "==> Pruning old images"
docker image prune -f

echo "==> Done. Current containers:"
$COMPOSE ps
