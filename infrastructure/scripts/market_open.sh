#!/usr/bin/env bash
# market_open.sh — NSE market open scan (9:15 AM IST / 3:45 AM UTC)
# Triggered by crontab: 45 3 * * 1-5
#
# Reads API_AUTH_TOKEN, LIVE_SUGGESTIONS_ASSETS, AUTO_EXECUTE_SIGNALS,
# TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, and API_URL from the .env.prod file
# (or the environment if already exported).
#
# Usage (manual):  ./infrastructure/scripts/market_open.sh
# Usage (cron):    Set via:  crontab -e   →  45 3 * * 1-5 /path/to/market_open.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_FILE="${LOG_FILE:-/var/log/trading-os/market_open.log}"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env.prod}"

# ── Load .env.prod if not already in env ────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
  _tmp=$(mktemp)
  grep -E '^[A-Z_][A-Z0-9_]*=' "$ENV_FILE" > "$_tmp" 2>/dev/null || true
  set -a
  # shellcheck disable=SC1090
  . "$_tmp"
  set +a
  rm -f "$_tmp"
fi

API_URL="${API_URL:-http://localhost:8000}"
API_AUTH_TOKEN="${API_AUTH_TOKEN:-}"
AUTO_EXECUTE_SIGNALS="${AUTO_EXECUTE_SIGNALS:-false}"
ASSETS="${LIVE_SUGGESTIONS_ASSETS:-RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,SBIN}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

send_telegram() {
  local msg="$1"
  [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ] && return 0
  curl -s -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="$TELEGRAM_CHAT_ID" \
    -d parse_mode="Markdown" \
    --data-urlencode text="$msg" \
    -o /dev/null || true
}

# ── Main ─────────────────────────────────────────────────────────────────────
mkdir -p "$(dirname "$LOG_FILE")"
log "=== NSE Market Open scan starting (AUTO_EXECUTE=$AUTO_EXECUTE_SIGNALS) ==="
send_telegram "🔔 *TradingOS*: NSE market open scan started\\nWatchlist: \`$ASSETS\`\\nAutoExecute: \`$AUTO_EXECUTE_SIGNALS\`"

IFS=',' read -ra ASSET_LIST <<< "$ASSETS"

SUMMARY=""
for ASSET in "${ASSET_LIST[@]}"; do
  ASSET="$(echo "$ASSET" | tr -d ' ')"
  [ -z "$ASSET" ] && continue

  EXECUTE="false"
  HEADERS=(-H "Content-Type: application/json")
  if [ "$AUTO_EXECUTE_SIGNALS" = "true" ] && [ -n "$API_AUTH_TOKEN" ]; then
    EXECUTE="true"
    HEADERS+=(-H "X-API-Key: $API_AUTH_TOKEN")
  fi

  log "Analyzing $ASSET (execute=$EXECUTE)..."
  RESPONSE=$(curl -s --max-time 120 \
    "${HEADERS[@]}" \
    -X POST "$API_URL/analyze" \
    -d "{\"asset\":\"$ASSET\",\"timeframe\":\"1d\",\"candle_limit\":300,\"execute_if_signal\":$EXECUTE}" \
    2>/dev/null || echo '{"error":"curl failed"}')

  ACTION=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('action','ERROR'))" 2>/dev/null || echo "ERROR")
  CONF=$(echo "$RESPONSE"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(round(d.get('confidence',0),1))" 2>/dev/null || echo "0")

  log "$ASSET → action=$ACTION confidence=$CONF"
  SUMMARY="${SUMMARY}\n• \`$ASSET\` → *$ACTION* (${CONF}%)"
done

log "=== Scan complete ==="
send_telegram "$(printf "📊 *TradingOS Market Open*\\n$SUMMARY")"
