#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${LODIA_SMOKE_BASE_URL:-http://127.0.0.1:18080}"
TOKEN="${LODIA_SMOKE_TOKEN:-}"

TMP_HEADERS="$(mktemp)"
trap 'rm -f "$TMP_HEADERS"' EXIT

curl_read() {
  local path="$1"
  if [[ -n "$TOKEN" ]]; then
    curl -fsS --connect-timeout 5 --max-time 20 -H "Authorization: Bearer ${TOKEN}" "${BASE_URL%/}${path}"
  else
    curl -fsS --connect-timeout 5 --max-time 20 "${BASE_URL%/}${path}"
  fi
}

curl_headers() {
  local path="$1"
  if [[ -n "$TOKEN" ]]; then
    curl -fsS --connect-timeout 5 --max-time 20 -H "Authorization: Bearer ${TOKEN}" -D "$TMP_HEADERS" -o /dev/null "${BASE_URL%/}${path}"
  else
    curl -fsS --connect-timeout 5 --max-time 20 -D "$TMP_HEADERS" -o /dev/null "${BASE_URL%/}${path}"
  fi
}

expect_ok() {
  local name="$1"
  local body="$2"
  if ! grep -q '"ok":true' <<<"$body"; then
    echo "deploy smoke failed: ${name} did not return ok=true" >&2
    echo "$body" >&2
    exit 1
  fi
}

echo "== Lodia deploy smoke against ${BASE_URL} =="

for _ in $(seq 1 90); do
  if ready="$(curl_read /api/ready 2>/dev/null)" && grep -q '"ok":true' <<<"$ready"; then
    break
  fi
  sleep 2
done

health="$(curl_read /api/health)"
expect_ok "health" "$health"

ready="$(curl_read /api/ready)"
expect_ok "ready" "$ready"

curl_headers /api/ready
grep -iq '^X-Request-ID:' "$TMP_HEADERS"

if grep -iq '^X-RateLimit-Limit:' "$TMP_HEADERS"; then
  echo "rate limit headers present"
else
  echo "rate limit headers not present; rate limiting may be disabled for this environment"
fi

migrations="$(curl_read /api/admin/migrations/status)"
expect_ok "migrations" "$migrations"

if [[ -n "$TOKEN" ]]; then
  observability="$(curl_read /api/admin/observability)"
  expect_ok "observability" "$observability"
else
  echo "LODIA_SMOKE_TOKEN is empty; skipped authenticated observability check"
fi

echo "deploy smoke ok"
