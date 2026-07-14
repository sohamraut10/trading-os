#!/usr/bin/env bash
# backup.sh — PostgreSQL backup for Trading OS
# Retention: 7 daily, 4 weekly (Sunday), 12 monthly (1st of month)
#
# Usage:
#   ./infrastructure/scripts/backup.sh [--dry-run]
#
# Environment (auto-detected from running containers if not set):
#   POSTGRES_HOST      default: localhost (if called from host) or postgres (from within Docker)
#   POSTGRES_PORT      default: 5432
#   POSTGRES_USER      default: trading
#   POSTGRES_PASSWORD  required
#   POSTGRES_DB        default: trading_os
#   BACKUP_DIR         default: /backups

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-/backups}"
PG_HOST="${POSTGRES_HOST:-localhost}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:-trading}"
PG_DB="${POSTGRES_DB:-trading_os}"
KEEP_DAILY=7
KEEP_WEEKLY=4
KEEP_MONTHLY=12
DRY_RUN=false

# ── Argument parsing ──────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    *) echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
err()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

run() {
  if $DRY_RUN; then
    log "DRY-RUN: $*"
  else
    "$@"
  fi
}

# ── Preflight ─────────────────────────────────────────────────────────────────
[ -z "${POSTGRES_PASSWORD:-}" ] && die "POSTGRES_PASSWORD is not set"
command -v pg_dump >/dev/null 2>&1 || die "pg_dump not found. Install postgresql-client."
command -v gzip    >/dev/null 2>&1 || die "gzip not found."

run mkdir -p "$BACKUP_DIR"

# ── Determine backup type ─────────────────────────────────────────────────────
DOW=$(date +%u)    # 1=Mon … 7=Sun
DOM=$(date +%d)    # day of month, zero-padded

if [ "$DOM" = "01" ]; then
  TYPE="monthly"
elif [ "$DOW" = "7" ]; then
  TYPE="weekly"
else
  TYPE="daily"
fi

TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
FILENAME="${TYPE}_trading_os_${TIMESTAMP}.sql.gz"
FILEPATH="$BACKUP_DIR/$FILENAME"

# ── Dump ──────────────────────────────────────────────────────────────────────
log "Starting $TYPE backup → $FILEPATH"

if $DRY_RUN; then
  log "DRY-RUN: would run pg_dump | gzip > $FILEPATH"
else
  PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
    --host="$PG_HOST" \
    --port="$PG_PORT" \
    --username="$PG_USER" \
    --dbname="$PG_DB" \
    --format=plain \
    --no-owner \
    --no-acl \
    | gzip -9 > "$FILEPATH"

  BACKUP_SIZE=$(du -sh "$FILEPATH" | cut -f1)
  log "Backup complete: $FILENAME ($BACKUP_SIZE)"
fi

# ── Retention pruning ─────────────────────────────────────────────────────────
prune() {
  local prefix="$1"
  local keep="$2"
  local files
  files=$(ls -1t "$BACKUP_DIR/${prefix}_trading_os_"*.sql.gz 2>/dev/null || true)
  local count
  count=$(echo "$files" | grep -c . 2>/dev/null || echo 0)

  if [ "$count" -le "$keep" ]; then
    log "Retention OK: $count $prefix backups (keep=$keep)"
    return
  fi

  local to_delete
  to_delete=$(echo "$files" | tail -n +"$((keep + 1))")
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    log "Pruning old $prefix backup: $(basename "$f")"
    run rm -f "$f"
  done <<< "$to_delete"
}

prune "daily"   "$KEEP_DAILY"
prune "weekly"  "$KEEP_WEEKLY"
prune "monthly" "$KEEP_MONTHLY"

# ── Summary ───────────────────────────────────────────────────────────────────
log "Backup summary:"
ls -lh "$BACKUP_DIR"/*.sql.gz 2>/dev/null \
  | awk '{printf "  %s  %s\n", $5, $9}' \
  || log "  (no backup files found — dry-run?)"

log "Done."
