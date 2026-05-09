#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${LODIA_ENV_FILE:-${1:-.env.production}}"
PROJECT_NAME="${LODIA_COMPOSE_PROJECT:-lodia}"
COMPOSE_FILE="${LODIA_COMPOSE_FILE:-docker-compose.prod.yml}"

if [[ "$ENV_FILE" != /* ]]; then
  ENV_FILE="${ROOT_DIR}/${ENV_FILE}"
fi
if [[ "$COMPOSE_FILE" != /* ]]; then
  COMPOSE_FILE="${ROOT_DIR}/${COMPOSE_FILE}"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  echo "create it from .env.production.example and fill production secrets first" >&2
  exit 64
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose v2 is required" >&2
  exit 127
fi

env_value() {
  local key="$1"
  awk -v key="$key" '
    $0 ~ /^[[:space:]]*#/ { next }
    $0 !~ "=" { next }
    {
      split($0, parts, "=")
      k=parts[1]
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
      if (k == key) {
        sub(/^[^=]*=/, "", $0)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0)
        gsub(/^"|"$/, "", $0)
        gsub(/^'\''|'\''$/, "", $0)
        print $0
        exit
      }
    }
  ' "$ENV_FILE"
}

compose() {
  docker compose -p "$PROJECT_NAME" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

cd "$ROOT_DIR"

echo "== Pulling Lodia images =="
compose pull

echo "== Starting Lodia project ${PROJECT_NAME} =="
compose up -d --remove-orphans

web_port="${LODIA_WEB_PORT:-$(env_value LODIA_WEB_PORT)}"
web_port="${web_port:-18080}"
smoke_base="${LODIA_SMOKE_BASE_URL:-http://127.0.0.1:${web_port}}"
smoke_token="${LODIA_SMOKE_TOKEN:-$(env_value LODIA_ADMIN_TOKEN)}"

echo "== Running read-only deploy smoke =="
LODIA_SMOKE_BASE_URL="$smoke_base" LODIA_SMOKE_TOKEN="$smoke_token" bash "$ROOT_DIR/scripts/deploy_smoke.sh"

if [[ "${LODIA_DEPLOY_FULL_SMOKE:-false}" == "true" ]]; then
  echo "== Running full write-path smoke =="
  LODIA_SMOKE_BASE_URL="$smoke_base" LODIA_SMOKE_TOKEN="$smoke_token" bash "$ROOT_DIR/scripts/go_smoke.sh"
fi

echo "Lodia deploy complete: ${smoke_base}"
