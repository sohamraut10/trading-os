#!/usr/bin/env bash
# deploy.sh — Zero-downtime deploy for Trading OS
#
# Used by CI/CD (GitHub Actions SSH step) and for manual deploys.
# Rebuilds only the api and frontend containers, then restarts them
# without touching postgres/redis/prometheus/grafana.
#
# Usage:
#   ./infrastructure/scripts/deploy.sh [--full]
#
# Flags:
#   --full    Rebuild and restart ALL services (not just api/frontend)
#
# Environment:
#   PROJECT_DIR    default: /Users/sohamraut/trading-os-1
#   COMPOSE_FILE   default: docker-compose.prod.yml
#   ENV_FILE       default: .env.prod

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/Users/sohamraut/trading-os-1}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"
FULL_DEPLOY=false
DEPLOY_SERVICES="api frontend"

for arg in "$@"; do
  case "$arg" in
    --full) FULL_DEPLOY=true; DEPLOY_SERVICES="" ;;
    *) echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

log()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
die()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ERROR: $*" >&2; exit 1; }

cd "$PROJECT_DIR" || die "Cannot cd to $PROJECT_DIR"

COMPOSE="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"

# ── 1. Pull latest code ────────────────────────────────────────────────────────
log "Pulling latest code from git..."
git fetch --prune
git pull --ff-only origin master || die "git pull failed. Resolve conflicts manually."
log "Git HEAD: $(git rev-parse --short HEAD)"

# ── 2. Build images ────────────────────────────────────────────────────────────
log "Building Docker images..."
if $FULL_DEPLOY; then
  $COMPOSE build --no-cache --pull
else
  $COMPOSE build --no-cache --pull $DEPLOY_SERVICES
fi
log "Build complete."

# ── 3. Take a backup before deploying ─────────────────────────────────────────
log "Running pre-deploy backup..."
BACKUP_DIR="${BACKUP_DIR:-/backups}"
POSTGRES_PASSWORD_VAL=$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2- | tr -d "'\"" || true)
if [ -n "$POSTGRES_PASSWORD_VAL" ]; then
  export POSTGRES_PASSWORD="$POSTGRES_PASSWORD_VAL"
  export BACKUP_DIR
  "$PROJECT_DIR/infrastructure/scripts/backup.sh" || log "WARNING: pre-deploy backup failed (continuing)"
else
  log "WARNING: POSTGRES_PASSWORD not found in $ENV_FILE — skipping backup"
fi

# ── 4. Rolling restart — only changed services ────────────────────────────────
log "Starting updated containers (no-deps)..."
if $FULL_DEPLOY; then
  $COMPOSE up -d --remove-orphans
else
  # shellcheck disable=SC2086
  $COMPOSE up -d --no-deps --remove-orphans $DEPLOY_SERVICES
fi

# ── 5. Health check ───────────────────────────────────────────────────────────
log "Waiting 15 s for containers to start..."
sleep 15

log "Running health check..."
if "$PROJECT_DIR/infrastructure/scripts/healthcheck.sh"; then
  log "Deploy successful. All services healthy."
else
  log "WARN: Some services are not fully healthy yet. Check logs:"
  echo "  docker compose -f $PROJECT_DIR/$COMPOSE_FILE logs --tail=50 api"
  echo "  docker compose -f $PROJECT_DIR/$COMPOSE_FILE logs --tail=50 frontend"
  # Don't fail the deploy outright — services may still be starting up
fi

# ── 6. Prune dangling images ──────────────────────────────────────────────────
log "Pruning dangling Docker images..."
docker image prune -f --filter "label!=watchtower" >/dev/null 2>&1 || true

log "Deploy complete. Git SHA: $(git rev-parse HEAD)"
