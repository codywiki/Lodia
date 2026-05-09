#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${SSH_TARGET:-}" ]]; then
  echo "Usage: SSH_TARGET=user@host [REMOTE_DIR=lodia] [LODIA_WEB_PORT=18080] [LODIA_BIND_HOST=127.0.0.1] [SSH_OPTS='-i key'] scripts/deploy-aliyun-safe.sh" >&2
  exit 2
fi

REMOTE_DIR="${REMOTE_DIR:-lodia}"
LODIA_WEB_PORT="${LODIA_WEB_PORT:-18080}"
LODIA_BIND_HOST="${LODIA_BIND_HOST:-127.0.0.1}"
PROJECT_NAME="${PROJECT_NAME:-lodia_prod}"
SSH_OPTS="${SSH_OPTS:-}"

random_secret() {
  openssl rand -hex 32
}

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required locally." >&2
  exit 2
fi
if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required locally for secret generation." >&2
  exit 2
fi

MYSQL_PASSWORD="${MYSQL_PASSWORD:-$(random_secret)}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-$(random_secret)}"
LODIA_ADMIN_TOKEN="${LODIA_ADMIN_TOKEN:-$(random_secret)}"
LODIA_REVIEWER_TOKEN="${LODIA_REVIEWER_TOKEN:-$(random_secret)}"
LODIA_CONTRIBUTOR_TOKEN="${LODIA_CONTRIBUTOR_TOKEN:-$(random_secret)}"
LODIA_PASSWORD_PEPPER="${LODIA_PASSWORD_PEPPER:-$(random_secret)}"
LODIA_ASYNC_PROCESSING="${LODIA_ASYNC_PROCESSING:-true}"
LODIA_RAW_OBJECT_TTL_HOURS="${LODIA_RAW_OBJECT_TTL_HOURS:-24}"
LODIA_MAX_REQUEST_BODY_BYTES="${LODIA_MAX_REQUEST_BODY_BYTES:-1048576}"
LODIA_RATE_LIMIT_ENABLED="${LODIA_RATE_LIMIT_ENABLED:-true}"
LODIA_RATE_LIMIT_REQUESTS="${LODIA_RATE_LIMIT_REQUESTS:-600}"
LODIA_RATE_LIMIT_WINDOW_SECONDS="${LODIA_RATE_LIMIT_WINDOW_SECONDS:-60}"
LODIA_TRUST_PROXY_HEADERS="${LODIA_TRUST_PROXY_HEADERS:-false}"
LODIA_ACCESS_LOG_ENABLED="${LODIA_ACCESS_LOG_ENABLED:-true}"
LODIA_DATASET_MAX_CASES="${LODIA_DATASET_MAX_CASES:-5000}"
LODIA_OBJECT_STORAGE_BACKEND="${LODIA_OBJECT_STORAGE_BACKEND:-local}"
LODIA_OBJECT_STORAGE_DIR="${LODIA_OBJECT_STORAGE_DIR:-/objects}"
LODIA_OSS_ENDPOINT="${LODIA_OSS_ENDPOINT:-}"
LODIA_OSS_BUCKET="${LODIA_OSS_BUCKET:-}"
LODIA_OSS_PREFIX="${LODIA_OSS_PREFIX:-lodia}"
LODIA_OSS_ACCESS_KEY_ID="${LODIA_OSS_ACCESS_KEY_ID:-}"
LODIA_OSS_ACCESS_KEY_SECRET="${LODIA_OSS_ACCESS_KEY_SECRET:-}"
ALIBABA_CLOUD_ACCESS_KEY_ID="${ALIBABA_CLOUD_ACCESS_KEY_ID:-}"
ALIBABA_CLOUD_ACCESS_KEY_SECRET="${ALIBABA_CLOUD_ACCESS_KEY_SECRET:-}"
LODIA_OBJECT_STORAGE_STS_ENABLED="${LODIA_OBJECT_STORAGE_STS_ENABLED:-false}"
LODIA_OSS_STS_ROLE_ARN="${LODIA_OSS_STS_ROLE_ARN:-}"
LODIA_OSS_STS_ENDPOINT_URL="${LODIA_OSS_STS_ENDPOINT_URL:-https://sts.aliyuncs.com}"
LODIA_OSS_STS_SESSION_NAME="${LODIA_OSS_STS_SESSION_NAME:-lodia-upload}"
LODIA_OSS_STS_DURATION_SECONDS="${LODIA_OSS_STS_DURATION_SECONDS:-900}"
REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"
PUBLIC_HOST="${PUBLIC_HOST:-${SSH_TARGET#*@}}"
LODIA_ALLOWED_ORIGINS="${LODIA_ALLOWED_ORIGINS:-http://127.0.0.1:${LODIA_WEB_PORT},http://localhost:${LODIA_WEB_PORT},http://${PUBLIC_HOST}:${LODIA_WEB_PORT}}"
MYSQL_DSN="${MYSQL_DSN:-lodia:${MYSQL_PASSWORD}@tcp(mysql:3306)/lodia?parseTime=true&charset=utf8mb4&loc=UTC}"

ssh_cmd() {
  # shellcheck disable=SC2086
  ssh ${SSH_OPTS} "$SSH_TARGET" "$@"
}

ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
cat > "$ENV_FILE" <<EOF
LODIA_BIND_HOST=${LODIA_BIND_HOST}
LODIA_WEB_PORT=${LODIA_WEB_PORT}

LODIA_ENV=production
LODIA_DEPLOYMENT_PROFILE=internal_test
LODIA_DATA_FOCUS=llm_long_horizon_task
LODIA_REGION=CN
LODIA_ALLOWED_ORIGINS=${LODIA_ALLOWED_ORIGINS}

MYSQL_PASSWORD=${MYSQL_PASSWORD}
MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD}
MYSQL_DSN=${MYSQL_DSN}
MYSQL_INNODB_BUFFER_POOL_SIZE=512M
MYSQL_MAX_CONNECTIONS=400

REDIS_URL=${REDIS_URL}
REDIS_MAXMEMORY=256mb
LODIA_WORKER_QUEUE=ingestion
LODIA_ASYNC_PROCESSING=${LODIA_ASYNC_PROCESSING}

LODIA_MAX_REQUEST_BODY_BYTES=${LODIA_MAX_REQUEST_BODY_BYTES}
LODIA_RATE_LIMIT_ENABLED=${LODIA_RATE_LIMIT_ENABLED}
LODIA_RATE_LIMIT_REQUESTS=${LODIA_RATE_LIMIT_REQUESTS}
LODIA_RATE_LIMIT_WINDOW_SECONDS=${LODIA_RATE_LIMIT_WINDOW_SECONDS}
LODIA_TRUST_PROXY_HEADERS=${LODIA_TRUST_PROXY_HEADERS}
LODIA_ACCESS_LOG_ENABLED=${LODIA_ACCESS_LOG_ENABLED}
LODIA_DATASET_MAX_CASES=${LODIA_DATASET_MAX_CASES}
LODIA_RAW_OBJECT_TTL_HOURS=${LODIA_RAW_OBJECT_TTL_HOURS}
LODIA_PURGE_RAW_AFTER_PROCESSING=true

LODIA_OBJECT_STORAGE_BACKEND=${LODIA_OBJECT_STORAGE_BACKEND}
LODIA_OBJECT_STORAGE_DIR=${LODIA_OBJECT_STORAGE_DIR}
LODIA_OSS_ENDPOINT=${LODIA_OSS_ENDPOINT}
LODIA_OSS_BUCKET=${LODIA_OSS_BUCKET}
LODIA_OSS_PREFIX=${LODIA_OSS_PREFIX}
LODIA_OSS_ACCESS_KEY_ID=${LODIA_OSS_ACCESS_KEY_ID}
LODIA_OSS_ACCESS_KEY_SECRET=${LODIA_OSS_ACCESS_KEY_SECRET}
LODIA_OBJECT_STORAGE_STS_ENABLED=${LODIA_OBJECT_STORAGE_STS_ENABLED}
LODIA_OSS_STS_ROLE_ARN=${LODIA_OSS_STS_ROLE_ARN}
LODIA_OSS_STS_ENDPOINT_URL=${LODIA_OSS_STS_ENDPOINT_URL}
LODIA_OSS_STS_SESSION_NAME=${LODIA_OSS_STS_SESSION_NAME}
LODIA_OSS_STS_DURATION_SECONDS=${LODIA_OSS_STS_DURATION_SECONDS}
ALIBABA_CLOUD_ACCESS_KEY_ID=${ALIBABA_CLOUD_ACCESS_KEY_ID}
ALIBABA_CLOUD_ACCESS_KEY_SECRET=${ALIBABA_CLOUD_ACCESS_KEY_SECRET}

LODIA_ADMIN_TOKEN=${LODIA_ADMIN_TOKEN}
LODIA_REVIEWER_TOKEN=${LODIA_REVIEWER_TOKEN}
LODIA_CONTRIBUTOR_TOKEN=${LODIA_CONTRIBUTOR_TOKEN}
LODIA_PASSWORD_PEPPER=${LODIA_PASSWORD_PEPPER}

PLATFORM_NET_MARGIN_RATE=0.20
EOF

echo "Preflight on ${SSH_TARGET}..."
ssh_cmd "set -e
  command -v docker >/dev/null
  docker compose version >/dev/null
  if ss -ltnH | awk '{print \$4}' | grep -Eq '(^|:)${LODIA_WEB_PORT}$'; then
    if ! docker ps --filter label=com.docker.compose.project='${PROJECT_NAME}' --filter label=com.docker.compose.service=web --format '{{.Ports}}' | grep -q ':${LODIA_WEB_PORT}->80'; then
      echo 'Port ${LODIA_WEB_PORT} is already in use by another service. Choose another LODIA_WEB_PORT.' >&2
      exit 20
    fi
    echo 'Port ${LODIA_WEB_PORT} is already used by the Lodia compose web container and will be replaced.'
  fi
  mkdir -p '${REMOTE_DIR}/storage/prod/mysql' '${REMOTE_DIR}/storage/prod/redis' '${REMOTE_DIR}/storage/prod/objects'
"

echo "Syncing project to ${SSH_TARGET}:${REMOTE_DIR}..."
RSYNC_RSH="ssh ${SSH_OPTS}" rsync -az --delete \
  --exclude='.git' \
  --exclude='.DS_Store' \
  --exclude='.venv' \
  --exclude='node_modules' \
  --exclude='apps/web/node_modules' \
  --exclude='apps/web/dist' \
  --exclude='storage' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='.playwright-cli' \
  --exclude='output' \
  ./ "$SSH_TARGET:${REMOTE_DIR}/"
RSYNC_RSH="ssh ${SSH_OPTS}" rsync -az "$ENV_FILE" "$SSH_TARGET:${REMOTE_DIR}/.env.production"

echo "Starting isolated Lodia compose project..."
ssh_cmd "set -e
  cd '${REMOTE_DIR}'
  docker compose -p '${PROJECT_NAME}' --env-file .env.production -f docker-compose.prod.yml up -d --build
  for i in \$(seq 1 60); do
    if curl -fsS 'http://127.0.0.1:${LODIA_WEB_PORT}/api/ready' >/dev/null 2>&1; then break; fi
    sleep 1
  done
  curl -fsS 'http://127.0.0.1:${LODIA_WEB_PORT}/api/ready'
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
