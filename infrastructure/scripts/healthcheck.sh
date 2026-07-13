#!/usr/bin/env bash
# healthcheck.sh — Print a status table for all Trading OS containers
# Compatible with bash 3.2+ (macOS default)
#
# Usage:
#   ./infrastructure/scripts/healthcheck.sh
#   ./infrastructure/scripts/healthcheck.sh --json

set -euo pipefail

JSON_MODE=false
for arg in "$@"; do
  case "$arg" in
    --json) JSON_MODE=true ;;
    *) echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

# Colour codes (disabled when not a terminal)
if [ -t 1 ] && ! $JSON_MODE; then
  GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
  RESET='\033[0m';    BOLD='\033[1m'
else
  GREEN=''; RED=''; YELLOW=''; RESET=''; BOLD=''
fi

COMPOSE_FILE="${COMPOSE_FILE:-$(cd "$(dirname "$0")/../.." && pwd)/docker-compose.prod.yml}"

container_status() { docker inspect --format '{{.State.Status}}' "$1" 2>/dev/null || echo "not found"; }
container_health()  { docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}N/A{{end}}' "$1" 2>/dev/null || echo "not found"; }

http_check() {
  local url="$1"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
  if [ "$code" = "200" ]; then echo "UP ($code)"; return 0
  else                         echo "DOWN ($code)"; return 1; fi
}

# Derive project name the same way docker compose does:
#   - COMPOSE_PROJECT_NAME env var, OR
#   - directory name of the compose file, lowercased, dashes kept
PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$(cd "$(dirname "$COMPOSE_FILE")" && pwd)" | tr '[:upper:]' '[:lower:]')}"
PREFIX="${PROJECT}-"

# Service list: "name:http_url" — empty url = docker-level health check only
# Prometheus/Grafana have no host-mapped ports; check via docker health only.
SERVICES="
caddy:http://localhost:8080/caddy-health
frontend:
api:
postgres:
redis:
prometheus:
grafana:
cloudflared:
"

if ! $JSON_MODE; then
  echo ""
  echo -e "${BOLD}Trading OS — Service Health  $(date -u +'%Y-%m-%d %H:%M UTC')${RESET}"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  printf "${BOLD}%-16s %-14s %-12s %-s${RESET}\n" "SERVICE" "CONTAINER" "HEALTH" "HTTP"
  echo "────────────────────────────────────────────────────────────────"
fi

ALL_OK=true
JSON_ROWS=""

while IFS=: read -r svc url; do
  [ -z "$svc" ] && continue
  CNAME="${PREFIX}${svc}-1"
  CSTATUS=$(container_status "$CNAME")
  CHEALTH=$(container_health "$CNAME")
  HTTP_RESULT="—"; HTTP_OK="n/a"

  if [ -n "$url" ]; then
    if HTTP_RESULT=$(http_check "$url" 2>/dev/null); then HTTP_OK="ok"
    else HTTP_OK="fail"; ALL_OK=false; fi
  fi

  [ "$CSTATUS" != "running" ]   && ALL_OK=false
  [ "$CHEALTH" = "unhealthy" ]  && ALL_OK=false

  if ! $JSON_MODE; then
    [ "$CSTATUS" = "running" ] && STATUS_COL="${GREEN}running${RESET}" || STATUS_COL="${RED}${CSTATUS}${RESET}"
    case "$CHEALTH" in
      healthy)   HEALTH_COL="${GREEN}healthy${RESET}"     ;;
      unhealthy) HEALTH_COL="${RED}unhealthy${RESET}"     ;;
      starting)  HEALTH_COL="${YELLOW}starting${RESET}"   ;;
      *)         HEALTH_COL="${RESET}${CHEALTH}${RESET}"  ;;
    esac
    [ "$HTTP_OK" = "ok" ]   && HTTP_COL="${GREEN}${HTTP_RESULT}${RESET}"  || true
    [ "$HTTP_OK" = "fail" ] && HTTP_COL="${RED}${HTTP_RESULT}${RESET}"    || true
    [ "$HTTP_OK" = "n/a" ]  && HTTP_COL="${RESET}—${RESET}"               || true
    printf "%-16s %-23b %-21b %-b\n" "$svc" "$STATUS_COL" "$HEALTH_COL" "$HTTP_COL"
  else
    ROW="{\"service\":\"$svc\",\"container_status\":\"$CSTATUS\",\"health\":\"$CHEALTH\",\"http\":\"$HTTP_OK\"}"
    JSON_ROWS="${JSON_ROWS:+$JSON_ROWS,}$ROW"
  fi
done <<EOF
$(echo "$SERVICES" | tr -d ' ')
EOF

if $JSON_MODE; then
  ALL_JSON=$( $ALL_OK && echo true || echo false )
  printf '{"timestamp":"%s","all_ok":%s,"services":[%s]}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ALL_JSON" "$JSON_ROWS"
else
  echo "────────────────────────────────────────────────────────────────"
  if $ALL_OK; then
    echo -e "${GREEN}All services healthy.${RESET}"
  else
    echo -e "${RED}One or more services are degraded. Check logs with:${RESET}"
    echo "  docker compose -f $COMPOSE_FILE logs --tail=50 <service>"
  fi
  echo ""
fi

$ALL_OK && exit 0 || exit 1
