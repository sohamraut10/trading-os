#!/usr/bin/env bash
# restore.sh — Restore Trading OS PostgreSQL from a backup file
#
# Usage:
#   ./infrastructure/scripts/restore.sh <backup-file.sql.gz>
#   ./infrastructure/scripts/restore.sh latest
#   ./infrastructure/scripts/restore.sh latest-daily
#   ./infrastructure/scripts/restore.sh latest-weekly
#   ./infrastructure/scripts/restore.sh latest-monthly
#
# Environment:
#   POSTGRES_HOST      default: localhost
#   POSTGRES_PORT      default: 5432
#   POSTGRES_USER      default: trading
#   POSTGRES_PASSWORD  required
#   POSTGRES_DB        default: trading_os
#   BACKUP_DIR         default: /backups

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
PG_HOST="${POSTGRES_HOST:-localhost}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:-trading}"
PG_DB="${POSTGRES_DB:-trading_os}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
err() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ERROR: $*" >&2; }
die() { err "$*"; exit 1; }

# ── Preflight ─────────────────────────────────────────────────────────────────
[ -z "${POSTGRES_PASSWORD:-}" ] && die "POSTGRES_PASSWORD is not set"
[ -z "${1:-}"                 ] && die "Usage: $0 <backup-file.sql.gz|latest|latest-daily|latest-weekly|latest-monthly>"
command -v psql   >/dev/null 2>&1 || die "psql not found. Install postgresql-client."
command -v gunzip >/dev/null 2>&1 || die "gunzip not found."

# ── Resolve backup file ───────────────────────────────────────────────────────
INPUT="$1"
case "$INPUT" in
  latest)          BACKUP_FILE=$(ls -1t "$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -1) ;;
  latest-daily)    BACKUP_FILE=$(ls -1t "$BACKUP_DIR"/daily_*.sql.gz   2>/dev/null | head -1) ;;
  latest-weekly)   BACKUP_FILE=$(ls -1t "$BACKUP_DIR"/weekly_*.sql.gz  2>/dev/null | head -1) ;;
  latest-monthly)  BACKUP_FILE=$(ls -1t "$BACKUP_DIR"/monthly_*.sql.gz 2>/dev/null | head -1) ;;
  *)               BACKUP_FILE="$INPUT" ;;
esac

[ -z "${BACKUP_FILE:-}" ] && die "No backup file found matching '$INPUT'"
[ -f "$BACKUP_FILE"     ] || die "Backup file not found: $BACKUP_FILE"

BACKUP_SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
log "Restore source: $BACKUP_FILE ($BACKUP_SIZE)"

# ── Safety prompt ─────────────────────────────────────────────────────────────
echo ""
echo "  WARNING: This will DROP and recreate the '$PG_DB' database."
echo "  Host:     $PG_HOST:$PG_PORT"
echo "  Database: $PG_DB"
echo "  File:     $BACKUP_FILE"
echo ""
read -r -p "  Type YES to proceed: " CONFIRM
[ "$CONFIRM" = "YES" ] || { log "Aborted."; exit 0; }

# ── Drop and recreate DB ──────────────────────────────────────────────────────
log "Dropping existing database..."
PGPASSWORD="$POSTGRES_PASSWORD" psql \
  --host="$PG_HOST" \
  --port="$PG_PORT" \
  --username="$PG_USER" \
  --dbname="postgres" \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${PG_DB}' AND pid <> pg_backend_pid();" \
  >/dev/null 2>&1 || true

PGPASSWORD="$POSTGRES_PASSWORD" psql \
  --host="$PG_HOST" \
  --port="$PG_PORT" \
  --username="$PG_USER" \
  --dbname="postgres" \
  -c "DROP DATABASE IF EXISTS ${PG_DB};" \
  >/dev/null

log "Creating fresh database..."
PGPASSWORD="$POSTGRES_PASSWORD" psql \
  --host="$PG_HOST" \
  --port="$PG_PORT" \
  --username="$PG_USER" \
  --dbname="postgres" \
  -c "CREATE DATABASE ${PG_DB} OWNER ${PG_USER};" \
  >/dev/null

# ── Restore ───────────────────────────────────────────────────────────────────
log "Restoring data (this may take a while)..."
gunzip -c "$BACKUP_FILE" | PGPASSWORD="$POSTGRES_PASSWORD" psql \
  --host="$PG_HOST" \
  --port="$PG_PORT" \
  --username="$PG_USER" \
  --dbname="$PG_DB" \
  --single-transaction \
  --quiet

log "Restore complete: $PG_DB is ready."
