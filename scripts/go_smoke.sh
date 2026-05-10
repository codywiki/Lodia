#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${LODIA_SMOKE_BASE_URL:-http://127.0.0.1:8080}"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required for scripts/go_smoke.sh" >&2
  exit 127
fi

HEADERS_FILE="$(mktemp)"
trap 'rm -f "$HEADERS_FILE"' EXIT

curl_auth() {
  if [[ -n "${LODIA_SMOKE_TOKEN:-}" ]]; then
    curl -fsS -H "Authorization: Bearer ${LODIA_SMOKE_TOKEN}" "$@"
  else
    curl -fsS "$@"
  fi
}

get_json() {
  curl_auth "$BASE_URL$1"
}

get_headers() {
  : > "$HEADERS_FILE"
  curl_auth -D "$HEADERS_FILE" -o /dev/null "$BASE_URL$1"
}

post_json() {
  local path="$1"
  local payload="$2"
  curl_auth -H "Content-Type: application/json" -X POST "$BASE_URL$path" -d "$payload"
}

portal_get_json() {
  local path="$1"
  local token="$2"
  curl -fsS -H "X-Lodia-Delivery-Token: $token" "$BASE_URL$path"
}

portal_post_json() {
  local path="$1"
  local token="$2"
  local payload="$3"
  curl -fsS -H "X-Lodia-Delivery-Token: $token" -H "Content-Type: application/json" -X POST "$BASE_URL$path" -d "$payload"
}

get_json_with_bearer() {
  local path="$1"
  local token="$2"
  curl -fsS -H "Authorization: Bearer $token" "$BASE_URL$path"
}

case_text='Goal: debug a long-running coding agent task from failing CI to merged fix.
Context: the user gave a repository, failing tests, constraints about not touching unrelated files, and required a clear verification record.
Constraints: preserve user changes, avoid leaking secrets, keep raw traces isolated, and only ship reusable evidence after review.
Plan:
1. inspect failing test output and recent code changes.
2. reproduce the failure locally.
3. patch the narrow module that owns the bug.
4. run unit tests, integration smoke, and review the diff.
Tool results: go test initially failed on duplicate canonical hash handling; after the patch go test ./... and the HTTP smoke passed.
Iteration notes: the first implementation missed an enterprise delivery usage callback, so the agent added a delivery grant token check and repeated the smoke.
Acceptance: all tests pass, redaction removed personal data, dataset export includes manifest and quality report, and the reviewer can trace why this case is useful.
Reusable rule: high-value long-horizon data must include goal, context, constraints, plan, tool evidence, failure path, correction, and acceptance criteria.'

echo "== Lodia Go smoke against $BASE_URL =="

for _ in $(seq 1 60); do
  if ready="$(get_json /api/ready 2>/dev/null)" && jq -e '.ok == true' >/dev/null <<<"$ready"; then
    break
  fi
  sleep 1
done

ready="$(get_json /api/ready)"
jq -e '.ok == true' >/dev/null <<<"$ready"

get_headers /api/admin/migrations/status
grep -iq '^X-Request-ID:' "$HEADERS_FILE"
grep -iq '^X-RateLimit-Limit:' "$HEADERS_FILE"

migration_status="$(get_json /api/admin/migrations/status)"
jq -e '.ok == true and .expected_count >= 4 and .applied_count >= 4' >/dev/null <<<"$migration_status"

observability="$(get_json /api/admin/observability)"
jq -e '.limits.rate_limit_enabled == true and .limits.rate_limit_requests >= 1 and .limits.access_log_enabled == true' >/dev/null <<<"$observability"

upload_credentials="$(post_json /api/object-storage/temporary-upload-credentials '{"key_prefix":"raw/smoke","expires_in_seconds":60}')"
jq -e '(.backend == "local" and .supported == false and .reason == "local_object_storage_uses_server_upload" and .credentials_mode == "server_upload_only") or (.backend == "oss" and (.key_prefix | type == "string") and (.expires_in_seconds >= 900))' >/dev/null <<<"$upload_credentials"

bootstrap="$(post_json /api/admin/internal-test/bootstrap '{"provider_mode":"mock","evidence_ref":"smoke"}')"
jq -e '(.seeded_provider_configs[0].id | length) > 0 and (.completed_compliance_tasks[0].id | length) > 0' >/dev/null <<<"$bootstrap"

sample_packs="$(get_json /api/admin/enterprise/sample-packs)"
jq -e '(.items | length) == 4 and any(.items[]; .task_type == "code_fix") and any(.items[]; .task_type == "model_eval_review")' >/dev/null <<<"$sample_packs"

smoke_email="smoke+$(date +%s)-$$@lodia.local"
smoke_password="SmokePass-$(date +%s)-$$"
smoke_owner="smoke_owner_$(date +%s)_$$"
case_text="${case_text}
Smoke run id: ${smoke_owner}."
user="$(post_json /api/admin/users "$(jq -nc --arg email "$smoke_email" --arg password "$smoke_password" '{email:$email, display_name:"Smoke Reviewer", role:"reviewer", password:$password}')")"
user_id="$(jq -r '.id' <<<"$user")"
if [[ -z "$user_id" || "$user_id" == "null" ]]; then
  echo "admin user creation failed" >&2
  echo "$user" | jq .
  exit 1
fi

login="$(post_json /api/auth/login "$(jq -nc --arg email "$smoke_email" --arg password "$smoke_password" '{email:$email, password:$password}')")"
login_token="$(jq -r '.token' <<<"$login")"
jq -e '.role == "reviewer" and (.token | length) > 20' >/dev/null <<<"$login"
review_auth="$(get_json_with_bearer /api/review/queue "$login_token")"
jq -e '.items | type == "array"' >/dev/null <<<"$review_auth"

issued="$(post_json "/api/admin/users/$user_id/tokens" '{"ttl_hours":24}')"
issued_token_id="$(jq -r '.id' <<<"$issued")"
jq -e '(.token | length) > 20 and .status == "active"' >/dev/null <<<"$issued"
revoked="$(post_json "/api/admin/tokens/$issued_token_id/revoke" '{}')"
jq -e '.status == "revoked"' >/dev/null <<<"$revoked"

submission="$(post_json "/api/submissions/text?sync=1" "$(jq -nc --arg owner "$smoke_owner" --arg text "$case_text" '{owner_id:$owner, text:$text, allowed_uses:["commercial_dataset","training"]}')")"
case_id="$(jq -r '.case.case_id // .case.id' <<<"$submission")"
case_status="$(jq -r '.case.status' <<<"$submission")"
case_ready="$(jq -r '.case.quality_gate.commercial_ready' <<<"$submission")"
if [[ -z "$case_id" || "$case_id" == "null" || "$case_ready" != "true" ]]; then
  echo "submission did not produce a commercial-ready long-horizon case" >&2
  echo "$submission" | jq .
  exit 1
fi

trace_export="$(post_json "/api/admin/trace-exports?sync=1" "$(jq -nc --arg owner "$smoke_owner" '{owner_id:$owner, source:"codex", external_id:("trace-smoke-"+$owner), title:("Smoke trace export "+$owner), allowed_uses:["commercial_dataset","training"], sync:true, trace:{objective:"package a reusable long-horizon agent repair trace", context:"the agent worked through a repository failure with CI evidence and user constraints", constraints:["preserve unrelated user edits","do not expose secrets","verify before delivery"], steps:["inspect failing logs","patch the owned module","rerun tests and smoke"], tool_results:["go test ./... passed","HTTP smoke passed"], failures:["initial implementation missed a settlement status check"], corrections:["added ledger settlement verification and reran smoke"], acceptance:["reviewer can inspect goal context steps tool evidence and verification"], reusable_rules:["valuable agent traces include objective context constraints steps tool results failure correction and acceptance evidence"]}, evidence_attachments:[{filename:"smoke-log.txt", media_type:"text/plain", content_base64:"c21va2UgbG9n"}]}')")"
jq -e '.status == "processed" and (.assets | length) == 1 and .case.quality_gate.commercial_ready == true' >/dev/null <<<"$trace_export"

dashboard="$(get_json "/api/contributor/dashboard?contributor_id=$smoke_owner")"
jq -e --arg owner "$smoke_owner" '.contributor_id == $owner and .cases.total >= 1 and (.source_trust.case_count >= 1)' >/dev/null <<<"$dashboard"
onboarding="$(get_json "/api/contributor/onboarding?contributor_id=$smoke_owner")"
jq -e --arg owner "$smoke_owner" '.contributor_id == $owner and .signals.active_authorization_count == 1' >/dev/null <<<"$onboarding"

samples="$(post_json /api/admin/review-samples/schedule '{"sample_type":"random_audit","limit":1,"min_drl":"DRL3","reason":"smoke"}')"
sample_id="$(jq -r '.items[0].id // empty' <<<"$samples")"
if [[ -z "$sample_id" ]]; then
  echo "review sample scheduling failed" >&2
  echo "$samples" | jq .
  exit 1
fi
completed_sample="$(post_json "/api/review-samples/$sample_id/complete" '{"decision":"passed","score":0.98,"notes":"smoke"}')"
jq -e '.status == "completed" and .decision == "passed"' >/dev/null <<<"$completed_sample"

dataset="$(post_json /api/datasets '{"name":"Go Smoke Dataset","purpose":"commercial_dataset","min_drl":"DRL3","gross_revenue_cents":100000,"direct_cost_cents":20000}')"
dataset_id="$(jq -r '.id' <<<"$dataset")"
if [[ -z "$dataset_id" || "$dataset_id" == "null" ]]; then
  echo "dataset creation failed" >&2
  echo "$dataset" | jq .
  exit 1
fi
jq -e '(.payout.usage_event_id | length) > 0 and .payout.contributor_pool_cents == 64000 and .payout.platform_share_cents == 16000 and (.payout.allocations | length) >= 1' >/dev/null <<<"$dataset"
while IFS= read -r dataset_case_id; do
  [[ -z "$dataset_case_id" ]] && continue
  dataset_safety="$(post_json "/api/admin/content-safety/case/$dataset_case_id/run" '{}')"
  jq -e '.status == "completed"' >/dev/null <<<"$dataset_safety"
done < <(jq -r '.case_ids[]' <<<"$dataset")

batch="$(post_json /api/ledger/payout-batches '{"min_amount_cents":100,"max_events":100}')"
batch_id="$(jq -r '.id' <<<"$batch")"
jq -e '.payout_count >= 1 and .total_amount_cents > 0' >/dev/null <<<"$batch"

customer="$(post_json /api/admin/enterprise/customers '{"name":"Smoke AI Lab","contact_email":"ops@smoke.example"}')"
customer_id="$(jq -r '.id' <<<"$customer")"

contract="$(post_json /api/admin/enterprise/contracts "$(jq -nc --arg customer_id "$customer_id" '{customer_id:$customer_id, terms_version:"smoke-v1", terms:{usage:"eval_only"}}')")"
contract_id="$(jq -r '.id' <<<"$contract")"

sso_provider="$(post_json /api/admin/sso-providers '{"tenant_id":"tenant_smoke","provider_type":"oidc","issuer":"https://issuer.smoke.example","domain":"smoke.example","metadata":{"client_id_suffix":"smoke"},"status":"testing"}')"
jq -e '.status == "testing" and .metadata.client_id_suffix == "smoke"' >/dev/null <<<"$sso_provider"

order="$(post_json /api/admin/enterprise/orders "$(jq -nc --arg customer_id "$customer_id" --arg contract_id "$contract_id" --arg dataset_id "$dataset_id" '{customer_id:$customer_id, contract_id:$contract_id, dataset_id:$dataset_id, gross_revenue_cents:100000, direct_cost_cents:20000, max_reads:3}')")"
order_id="$(jq -r '.id' <<<"$order")"

recognized="$(post_json "/api/admin/enterprise/orders/$order_id/recognize-usage" '{}')"
jq -e '.status == "revenue_recognized"' >/dev/null <<<"$recognized"

invoice="$(post_json /api/admin/invoices "$(jq -nc --arg order_id "$order_id" '{order_id:$order_id, invoice_no:"INV-2026-SMOKE", amount_cents:100000, tax_cents:6000}')")"
invoice_id="$(jq -r '.id' <<<"$invoice")"
paid_invoice="$(post_json "/api/admin/invoices/$invoice_id/paid" '{}')"
jq -e '.status == "paid" and .amount_cents == 100000' >/dev/null <<<"$paid_invoice"

reconciliation="$(post_json /api/admin/reconciliation "$(jq -nc --arg order_id "$order_id" '{scope_type:"enterprise_order", scope_id:$order_id}')")"
jq -e '.status == "balanced" and .summary.anomaly_count == 0' >/dev/null <<<"$reconciliation"

dispute="$(post_json /api/admin/disputes "$(jq -nc --arg order_id "$order_id" '{entity_type:"enterprise_order", entity_id:$order_id, reason:"smoke", hold_payouts:true}')")"
jq -e '.status == "open" and .held_payout_count == 1' >/dev/null <<<"$dispute"

grant="$(post_json "/api/admin/datasets/$dataset_id/delivery-grants" "$(jq -nc --arg customer_id "$customer_id" --arg order_id "$order_id" '{customer_id:$customer_id, order_id:$order_id, max_reads:3}')")"
grant_id="$(jq -r '.id' <<<"$grant")"
delivery_token="$(jq -r '.delivery_token' <<<"$grant")"
if [[ -z "$delivery_token" || "$delivery_token" == "null" ]]; then
  echo "delivery grant did not return a one-time delivery token" >&2
  echo "$grant" | jq .
  exit 1
fi

portal="$(portal_get_json "/api/enterprise/portal/$grant_id" "$delivery_token")"
jq -e --arg dataset_id "$dataset_id" '.dataset.id == $dataset_id' >/dev/null <<<"$portal"

usage="$(portal_post_json "/api/enterprise/portal/$grant_id/usage-reports" "$delivery_token" "$(jq -nc --arg grant_id "$grant_id" '{grant_id:$grant_id, external_event_id:"smoke-usage-1", reported_case_count:1, payload:{purpose:"portal_smoke"}}')")"
jq -e '.status == "recorded"' >/dev/null <<<"$usage"

inbox="$(post_json /api/admin/inboxes "$(jq -nc --arg owner "$smoke_owner" '{owner_id:$owner, allowed_uses:["commercial_dataset","training"]}')")"
inbox_address="$(jq -r '.address' <<<"$inbox")"
inbound="$(post_json /api/admin/inbound/messages "$(jq -nc --arg recipient "$inbox_address" --arg text "$case_text" '{recipient:$recipient, message_id:"smoke-message-1", sender:"contributor@smoke.example", subject:"Smoke long-horizon case", body_text:$text, enqueue:false}')")"
jq -e '.submission_id | length > 0' >/dev/null <<<"$inbound"

payout_profile="$(post_json "/api/admin/payout-profiles/$smoke_owner" '{"country_region":"CN","account_type":"bank","account_reference":"6222020202020202","kyc_status":"verified","tax_status":"verified","risk_status":"clear"}')"
jq -e '.status == "active" and .account_ref_suffix == "0202" and (.account_ref_hash | length) == 12' >/dev/null <<<"$payout_profile"

webhook="$(post_json /api/admin/webhook-cases "$(jq -nc --arg owner "$smoke_owner" --arg text "$case_text" '{source:"codex", external_id:"smoke-webhook-1", owner_id:$owner, text:$text, allowed_uses:["commercial_dataset","training"], enqueue:false}')")"
jq -e '.submission_id | length > 0' >/dev/null <<<"$webhook"

transfer="$(post_json /api/admin/payout-transfers "$(jq -nc --arg batch_id "$batch_id" '{batch_id:$batch_id, provider_name:"mock_payout"}')")"
transfer_id="$(jq -r '.id' <<<"$transfer")"
confirmed="$(post_json "/api/admin/payout-transfers/$transfer_id/confirm" '{"status":"succeeded","external_reference":"smoke-transfer","response":{"ok":true}}')"
jq -e '.status == "succeeded"' >/dev/null <<<"$confirmed"
settled_batch="$(post_json "/api/ledger/payout-batches/$batch_id/settle" '{}')"
jq -e '.status == "settled"' >/dev/null <<<"$settled_batch"
payout_metrics="$(get_json /api/admin/metrics)"
jq -e '.payouts.settled >= 1 and .pending_payout_cents >= 0' >/dev/null <<<"$payout_metrics"

dsr="$(post_json /api/admin/dsr "$(jq -nc --arg owner "$smoke_owner" '{owner_id:$owner, request_type:"delete", reason:"smoke"}')")"
dsr_id="$(jq -r '.id' <<<"$dsr")"
fulfilled="$(post_json "/api/admin/dsr/$dsr_id/fulfill" '{}')"
jq -e '.status == "fulfilled"' >/dev/null <<<"$fulfilled"

owner_authorizations="$(get_json "/api/authorizations?contributor_id=$smoke_owner")"
owner_auth_id="$(jq -r '.items[0].id' <<<"$owner_authorizations")"
withdrawal="$(post_json "/api/authorizations/$owner_auth_id/withdraw" '{}')"
jq -e --arg owner_auth_id "$owner_auth_id" '.status == "withdrawn" and .authorization_id == $owner_auth_id' >/dev/null <<<"$withdrawal"
authorizations="$(get_json "/api/authorizations?contributor_id=$smoke_owner")"
jq -e '.items[0].status == "withdrawn"' >/dev/null <<<"$authorizations"

safety="$(post_json "/api/admin/content-safety/case/$case_id/run" '{}')"
jq -e '.status == "completed"' >/dev/null <<<"$safety"

evaluation="$(post_json "/api/admin/datasets/$dataset_id/evaluate" '{}')"
jq -e '.status == "completed" and .metrics.critical_count == 0 and .metrics.holdout_overlap_count == 0 and .metrics.ready_for_commercial_delivery == true and (.metrics.readiness_score >= 0.5)' >/dev/null <<<"$evaluation"

proof="$(get_json "/api/admin/datasets/$dataset_id/commercial-proof")"
jq -e --arg owner "$smoke_owner" '.case_count >= 1 and (.proof_hash | length) == 64 and .commercial_checks.artifact_hashes_present == true and .commercial_checks.content_safety_passed == true and .commercial_checks.dataset_evaluation_completed == true and .commercial_checks.dataset_evaluation_passed == true and .commercial_checks.dataset_evaluation_critical == 0 and .commercial_checks.all_authorizations_active == false and .ready_for_commercial_delivery == false and (.artifact_hashes.data.sha256 | length) == 64 and (.blocked_reasons | index("authorization_withdrawn")) and any(.authorization_checks[]; .owner_id == $owner and .status == "withdrawn")' >/dev/null <<<"$proof"

readiness="$(get_json /api/admin/launch-readiness)"
jq -e '.signals.schema_migrations_ok == true and .signals.active_provider_configs >= 1 and .signals.completed_compliance_tasks >= 1' >/dev/null <<<"$readiness"

alerts="$(get_json /api/admin/operational-alerts)"
jq -e '.ok == true and (.items | type == "array") and (.critical_count == 0)' >/dev/null <<<"$alerts"

echo "ok user=$user_id case=$case_id status=$case_status dataset=$dataset_id batch=$batch_id order=$order_id grant=$grant_id"
