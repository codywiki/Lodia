#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${SSH_TARGET:-}" ]]; then
  echo "Usage: SSH_TARGET=user@host [REMOTE_DIR=lodia] [LODIA_WEB_PORT=18080] [LODIA_BIND_HOST=127.0.0.1] scripts/deploy-aliyun-safe.sh" >&2
  exit 2
fi

REMOTE_DIR="${REMOTE_DIR:-lodia}"
LODIA_WEB_PORT="${LODIA_WEB_PORT:-18080}"
LODIA_BIND_HOST="${LODIA_BIND_HOST:-127.0.0.1}"
PROJECT_NAME="${PROJECT_NAME:-lodia_prod}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}"
LODIA_ADMIN_TOKEN="${LODIA_ADMIN_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}"
LODIA_REVIEWER_TOKEN="${LODIA_REVIEWER_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}"
LODIA_CONTRIBUTOR_TOKEN="${LODIA_CONTRIBUTOR_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}"
LODIA_ASYNC_PROCESSING="${LODIA_ASYNC_PROCESSING:-false}"
LODIA_OBJECT_STORAGE_BACKEND="${LODIA_OBJECT_STORAGE_BACKEND:-local}"
LODIA_OBJECT_STORAGE_DIR="${LODIA_OBJECT_STORAGE_DIR:-/objects}"

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required locally." >&2
  exit 2
fi

ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
cat > "$ENV_FILE" <<EOF
LODIA_BIND_HOST=${LODIA_BIND_HOST}
LODIA_WEB_PORT=${LODIA_WEB_PORT}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
LODIA_ADMIN_TOKEN=${LODIA_ADMIN_TOKEN}
LODIA_REVIEWER_TOKEN=${LODIA_REVIEWER_TOKEN}
LODIA_CONTRIBUTOR_TOKEN=${LODIA_CONTRIBUTOR_TOKEN}
LODIA_ASYNC_PROCESSING=${LODIA_ASYNC_PROCESSING}
LODIA_OBJECT_STORAGE_BACKEND=${LODIA_OBJECT_STORAGE_BACKEND}
LODIA_OBJECT_STORAGE_DIR=${LODIA_OBJECT_STORAGE_DIR}
EOF

echo "Preflight on ${SSH_TARGET}..."
ssh "$SSH_TARGET" "set -e
  command -v docker >/dev/null
  docker compose version >/dev/null
  if ss -ltnH | awk '{print \$4}' | grep -Eq '(^|:)${LODIA_WEB_PORT}$'; then
    echo 'Port ${LODIA_WEB_PORT} is already in use. Choose another LODIA_WEB_PORT.' >&2
    exit 20
  fi
  mkdir -p '${REMOTE_DIR}/storage/prod'
"

echo "Syncing project to ${SSH_TARGET}:${REMOTE_DIR}..."
rsync -az --delete \
  --exclude='.git' \
  --exclude='.DS_Store' \
  --exclude='.venv' \
  --exclude='node_modules' \
  --exclude='apps/web/node_modules' \
  --exclude='apps/web/dist' \
  --exclude='storage' \
  --exclude='.env' \
  --exclude='.env.*' \
  ./ "$SSH_TARGET:${REMOTE_DIR}/"
rsync -az "$ENV_FILE" "$SSH_TARGET:${REMOTE_DIR}/.env.production"

echo "Starting isolated Lodia compose project..."
ssh "$SSH_TARGET" "set -e
  cd '${REMOTE_DIR}'
  docker compose -p '${PROJECT_NAME}' --env-file .env.production -f docker-compose.prod.yml up -d --build
  curl -fsS 'http://127.0.0.1:${LODIA_WEB_PORT}/api/health'
  curl -fsSI 'http://127.0.0.1:${LODIA_WEB_PORT}/' >/dev/null
"

cat <<EOF
Lodia deployed without touching existing reverse proxies or system services.

Remote directory: ${REMOTE_DIR}
Compose project:  ${PROJECT_NAME}
Bind address:     ${LODIA_BIND_HOST}
Port:             ${LODIA_WEB_PORT}
Admin token:      stored in ${REMOTE_DIR}/.env.production

Local verification:
  ssh -L ${LODIA_WEB_PORT}:127.0.0.1:${LODIA_WEB_PORT} ${SSH_TARGET}
  open http://127.0.0.1:${LODIA_WEB_PORT}

If you intentionally want public access, rerun with:
  LODIA_BIND_HOST=0.0.0.0 LODIA_WEB_PORT=${LODIA_WEB_PORT} SSH_TARGET=${SSH_TARGET} scripts/deploy-aliyun-safe.sh
EOF
