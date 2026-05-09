#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${SSH_TARGET:-}" ]]; then
  echo "Usage: SSH_TARGET=user@host [REMOTE_DIR=lodia] [LODIA_WEB_PORT=18080] [LODIA_BIND_HOST=127.0.0.1] [SSH_OPTS='-i key'] scripts/deploy-aliyun-podman-safe.sh" >&2
  exit 2
fi

REMOTE_DIR="${REMOTE_DIR:-lodia}"
LODIA_WEB_PORT="${LODIA_WEB_PORT:-18080}"
LODIA_BIND_HOST="${LODIA_BIND_HOST:-127.0.0.1}"
SSH_OPTS="${SSH_OPTS:-}"

EXISTING_REMOTE_ENV="$(
  # shellcheck disable=SC2086
  ssh ${SSH_OPTS} "$SSH_TARGET" "if [ -f '${REMOTE_DIR}/.env.production' ]; then awk 'BEGIN{FS=\"=\"} /^(MYSQL_PASSWORD|MYSQL_ROOT_PASSWORD|MYSQL_DSN|LODIA_ADMIN_TOKEN|LODIA_REVIEWER_TOKEN|LODIA_CONTRIBUTOR_TOKEN|LODIA_PASSWORD_PEPPER)=/ {key=\$1; sub(\"^[^=]*=\", \"\"); print key \"=\" \$0}' '${REMOTE_DIR}/.env.production'; fi" 2>/dev/null || true
)"

env_value() {
  local key="$1"
  printf '%s\n' "$EXISTING_REMOTE_ENV" | awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); value = $0} END {print value}'
}

random_secret() {
  openssl rand -hex 32
}

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required locally for secret generation." >&2
  exit 2
fi
if ! command -v tar >/dev/null 2>&1; then
  echo "tar is required locally." >&2
  exit 2
fi

MYSQL_PASSWORD="${MYSQL_PASSWORD:-$(env_value MYSQL_PASSWORD)}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-$(random_secret)}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-$(env_value MYSQL_ROOT_PASSWORD)}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-$(random_secret)}"
LODIA_ADMIN_TOKEN="${LODIA_ADMIN_TOKEN:-$(env_value LODIA_ADMIN_TOKEN)}"
LODIA_ADMIN_TOKEN="${LODIA_ADMIN_TOKEN:-$(random_secret)}"
LODIA_REVIEWER_TOKEN="${LODIA_REVIEWER_TOKEN:-$(env_value LODIA_REVIEWER_TOKEN)}"
LODIA_REVIEWER_TOKEN="${LODIA_REVIEWER_TOKEN:-$(random_secret)}"
LODIA_CONTRIBUTOR_TOKEN="${LODIA_CONTRIBUTOR_TOKEN:-$(env_value LODIA_CONTRIBUTOR_TOKEN)}"
LODIA_CONTRIBUTOR_TOKEN="${LODIA_CONTRIBUTOR_TOKEN:-$(random_secret)}"
LODIA_PASSWORD_PEPPER="${LODIA_PASSWORD_PEPPER:-$(env_value LODIA_PASSWORD_PEPPER)}"
LODIA_PASSWORD_PEPPER="${LODIA_PASSWORD_PEPPER:-$(random_secret)}"
MYSQL_DSN="${MYSQL_DSN:-$(env_value MYSQL_DSN)}"
MYSQL_DSN="${MYSQL_DSN:-lodia:${MYSQL_PASSWORD}@tcp(mysql:3306)/lodia?parseTime=true&charset=utf8mb4&loc=UTC}"
PUBLIC_HOST="${PUBLIC_HOST:-${SSH_TARGET#*@}}"
LODIA_ALLOWED_ORIGINS="${LODIA_ALLOWED_ORIGINS:-http://127.0.0.1:${LODIA_WEB_PORT},http://localhost:${LODIA_WEB_PORT},http://${PUBLIC_HOST}:${LODIA_WEB_PORT}}"

GO_IMAGE="${GO_IMAGE:-docker.m.daocloud.io/library/golang:1.22-alpine}"
ALPINE_IMAGE="${ALPINE_IMAGE:-docker.m.daocloud.io/library/alpine:3.20}"
NODE_IMAGE="${NODE_IMAGE:-docker.m.daocloud.io/library/node:22-alpine}"
NGINX_IMAGE="${NGINX_IMAGE:-docker.m.daocloud.io/library/nginx:1.27-alpine}"
MYSQL_IMAGE="${MYSQL_IMAGE:-docker.m.daocloud.io/library/mysql:8.4}"
REDIS_IMAGE="${REDIS_IMAGE:-docker.m.daocloud.io/library/redis:7-alpine}"
LODIA_RESET_DATA="${LODIA_RESET_DATA:-false}"
LODIA_BUILD_NO_CACHE="${LODIA_BUILD_NO_CACHE:-false}"
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
BUILD_FLAGS=""
if [[ "$LODIA_BUILD_NO_CACHE" == "true" ]]; then
  BUILD_FLAGS="--no-cache"
fi

ssh_cmd() {
  # shellcheck disable=SC2086
  ssh ${SSH_OPTS} "$SSH_TARGET" "$@"
}

ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
cat > "$ENV_FILE" <<EOF
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

REDIS_URL=redis://redis:6379/0
REDIS_MAXMEMORY=256mb
LODIA_WORKER_QUEUE=ingestion
LODIA_ASYNC_PROCESSING=true

LODIA_MAX_REQUEST_BODY_BYTES=1048576
LODIA_RATE_LIMIT_ENABLED=true
LODIA_RATE_LIMIT_REQUESTS=600
LODIA_RATE_LIMIT_WINDOW_SECONDS=60
LODIA_TRUST_PROXY_HEADERS=false
LODIA_ACCESS_LOG_ENABLED=true
LODIA_DATASET_MAX_CASES=5000
LODIA_RAW_OBJECT_TTL_HOURS=24
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
  command -v podman >/dev/null
  if ss -ltnH | awk '{print \$4}' | grep -Eq '(^|:)${LODIA_WEB_PORT}$'; then
    if ! podman port lodia-web 2>/dev/null | grep -Eq '(^|:)${LODIA_WEB_PORT}$'; then
      echo 'Port ${LODIA_WEB_PORT} is already in use by another service. Choose another LODIA_WEB_PORT.' >&2
      exit 20
    fi
    echo 'Port ${LODIA_WEB_PORT} is already used by lodia-web and will be replaced.'
  fi
  mkdir -p '${REMOTE_DIR}/storage/prod/mysql' '${REMOTE_DIR}/storage/prod/redis' '${REMOTE_DIR}/storage/prod/objects'
"

echo "Syncing project to ${SSH_TARGET}:${REMOTE_DIR}..."
if command -v rsync >/dev/null 2>&1 && ssh_cmd "command -v rsync >/dev/null 2>&1"; then
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
else
  COPYFILE_DISABLE=1 tar --no-xattrs \
    --exclude='./.git' \
    --exclude='./.DS_Store' \
    --exclude='./._*' \
    --exclude='*/._*' \
    --exclude='./.venv' \
    --exclude='./node_modules' \
    --exclude='./apps/web/node_modules' \
    --exclude='./apps/web/dist' \
    --exclude='./storage' \
    --exclude='./.env' \
    --exclude='./.env.*' \
    --exclude='./.playwright-cli' \
    --exclude='./output' \
    -czf - . | ssh_cmd "set -e; mkdir -p '${REMOTE_DIR}'; cd '${REMOTE_DIR}'; tar -xzf -"
  ssh_cmd "cat > '${REMOTE_DIR}/.env.production'" < "$ENV_FILE"
fi

echo "Building and starting isolated Lodia Podman containers..."
ssh_cmd "set -e
  cd '${REMOTE_DIR}'
  find . -name '._*' -delete
  if [[ '${LODIA_RESET_DATA}' == 'true' ]]; then
    rm -rf storage/prod/mysql storage/prod/redis storage/prod/objects
    mkdir -p storage/prod/mysql storage/prod/redis storage/prod/objects
  fi
  podman network inspect lodia-net >/dev/null 2>&1 || podman network create lodia-net >/dev/null
  podman rm -f lodia-web lodia-api lodia-worker lodia-mysql lodia-redis >/dev/null 2>&1 || true
  podman build ${BUILD_FLAGS} --build-arg GO_IMAGE='${GO_IMAGE}' --build-arg ALPINE_IMAGE='${ALPINE_IMAGE}' -t lodia-api-go:local apps/api-go
  podman build ${BUILD_FLAGS} --build-arg NODE_IMAGE='${NODE_IMAGE}' --build-arg NGINX_IMAGE='${NGINX_IMAGE}' -t lodia-web:local apps/web
  podman run -d --name lodia-mysql --network lodia-net --network-alias mysql --restart=always \
    -e MYSQL_DATABASE=lodia -e MYSQL_USER=lodia -e MYSQL_PASSWORD='${MYSQL_PASSWORD}' -e MYSQL_ROOT_PASSWORD='${MYSQL_ROOT_PASSWORD}' \
    -v \"\$(pwd)/storage/prod/mysql:/var/lib/mysql\" \
    '${MYSQL_IMAGE}' --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci --innodb-buffer-pool-size=256M >/dev/null
  podman run -d --name lodia-redis --network lodia-net --network-alias redis --restart=always \
    -v \"\$(pwd)/storage/prod/redis:/data\" \
    '${REDIS_IMAGE}' redis-server --appendonly yes >/dev/null
  for i in \$(seq 1 90); do
    if podman exec lodia-mysql mysqladmin ping -h 127.0.0.1 -u lodia -p'${MYSQL_PASSWORD}' --silent >/dev/null 2>&1; then break; fi
    sleep 1
  done
  podman run -d --name lodia-api --network lodia-net --network-alias api --restart=always \
    --env-file .env.production \
    -v \"\$(pwd)/storage/prod/objects:/objects\" \
    lodia-api-go:local >/dev/null
  podman run -d --name lodia-worker --network lodia-net --restart=always \
    --env-file .env.production \
    -v \"\$(pwd)/storage/prod/objects:/objects\" \
    --entrypoint /app/lodia-worker \
    lodia-api-go:local >/dev/null
  podman run -d --name lodia-web --network lodia-net --restart=always \
    -p '${LODIA_BIND_HOST}:${LODIA_WEB_PORT}:80' \
    lodia-web:local >/dev/null
  for i in \$(seq 1 60); do
    if curl -fsS 'http://127.0.0.1:${LODIA_WEB_PORT}/api/ready' >/dev/null 2>&1; then break; fi
    sleep 1
  done
  curl -fsS 'http://127.0.0.1:${LODIA_WEB_PORT}/api/ready'
  curl -fsSI 'http://127.0.0.1:${LODIA_WEB_PORT}/' >/dev/null
"

cat <<EOF
Lodia deployed with isolated Podman containers.

Remote directory: ${REMOTE_DIR}
Containers:       lodia-mysql, lodia-redis, lodia-api, lodia-worker, lodia-web
Bind address:     ${LODIA_BIND_HOST}
Port:             ${LODIA_WEB_PORT}
Admin token:      stored in ${REMOTE_DIR}/.env.production
EOF
