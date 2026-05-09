package httpapi

import (
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/codywiki/lodia/apps/api-go/internal/annotation"
	"github.com/codywiki/lodia/apps/api-go/internal/auth"
	"github.com/codywiki/lodia/apps/api-go/internal/config"
	"github.com/codywiki/lodia/apps/api-go/internal/focus"
	"github.com/codywiki/lodia/apps/api-go/internal/jobqueue"
	"github.com/codywiki/lodia/apps/api-go/internal/objectstore"
	"github.com/codywiki/lodia/apps/api-go/internal/redaction"
	"github.com/codywiki/lodia/apps/api-go/internal/store"
)

type Server struct {
	cfg       config.Config
	db        *store.DB
	objects   objectstore.Store
	queue     *jobqueue.RedisQueue
	processor Processor
	limiter   *rateLimiter
}

func New(cfg config.Config, db *store.DB, objects objectstore.Store, queue *jobqueue.RedisQueue) *Server {
	return &Server{cfg: cfg, db: db, objects: objects, queue: queue, processor: NewProcessor(cfg, db, objects), limiter: newRateLimiter(cfg.RateLimitRequests, cfg.RateLimitWindow)}
}

func (s *Server) Router() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/health", s.health)
	mux.HandleFunc("GET /api/ready", s.ready)
	mux.HandleFunc("POST /api/auth/login", s.login)
	mux.HandleFunc("GET /api/admin/users", s.listUsers)
	mux.HandleFunc("POST /api/admin/users", s.createUser)
	mux.HandleFunc("POST /api/admin/users/{id}/tokens", s.issueUserToken)
	mux.HandleFunc("POST /api/admin/tokens/{id}/revoke", s.revokeAuthToken)
	mux.HandleFunc("POST /api/pipeline/preview", s.preview)
	mux.HandleFunc("POST /api/submissions/text", s.createTextSubmission)
	mux.HandleFunc("GET /api/submissions/{id}", s.getSubmission)
	mux.HandleFunc("GET /api/cases", s.listCases)
	mux.HandleFunc("GET /api/review/queue", s.reviewQueue)
	mux.HandleFunc("POST /api/review/claim", s.claimReview)
	mux.HandleFunc("POST /api/review/{id}/release", s.releaseReview)
	mux.HandleFunc("POST /api/review/{id}/approve", s.approveCase)
	mux.HandleFunc("POST /api/review/{id}/reject", s.rejectCase)
	mux.HandleFunc("POST /api/review/{id}/expert-verify", s.expertVerify)
	mux.HandleFunc("POST /api/review/{id}/gold-review", s.goldReview)
	mux.HandleFunc("GET /api/review/{id}/long-horizon", s.getLongHorizon)
	mux.HandleFunc("POST /api/review/{id}/long-horizon", s.saveLongHorizon)
	mux.HandleFunc("GET /api/datasets", s.listDatasets)
	mux.HandleFunc("POST /api/datasets", s.createDataset)
	mux.HandleFunc("GET /api/datasets/{id}/contract", s.datasetContract)
	mux.HandleFunc("GET /api/datasets/{id}/artifacts/{artifact}", s.datasetArtifact)
	mux.HandleFunc("GET /api/audit/logs", s.auditLogs)
	mux.HandleFunc("GET /api/admin/metrics", s.metrics)
	mux.HandleFunc("GET /api/admin/observability", s.observability)
	mux.HandleFunc("POST /api/assets", s.createAsset)
	mux.HandleFunc("GET /api/assets", s.listAssets)
	mux.HandleFunc("POST /api/assets/{id}/extract", s.extractAsset)
	mux.HandleFunc("GET /api/authorizations", s.authorizations)
	mux.HandleFunc("POST /api/authorizations/{id}/withdraw", s.withdrawAuthorization)
	mux.HandleFunc("GET /api/contributor/dashboard", s.contributorDashboard)
	mux.HandleFunc("GET /api/contributor/onboarding", s.contributorOnboarding)
	mux.HandleFunc("POST /api/contributor/payout-profile", s.submitContributorPayoutProfile)
	mux.HandleFunc("POST /api/trace-exports", s.ingestTraceExport)
	mux.HandleFunc("POST /api/ledger/payout-batches", s.createPayoutBatch)
	mux.HandleFunc("POST /api/ledger/payout-batches/{id}/settle", s.settlePayoutBatch)
	mux.HandleFunc("POST /api/admin/enterprise/customers", s.createEnterpriseCustomer)
	mux.HandleFunc("GET /api/admin/enterprise/customers", s.listEnterpriseCustomers)
	mux.HandleFunc("GET /api/admin/enterprise/sample-packs", s.enterpriseSamplePacks)
	mux.HandleFunc("POST /api/admin/enterprise/contracts", s.createEnterpriseContract)
	mux.HandleFunc("POST /api/admin/enterprise/orders", s.createEnterpriseOrder)
	mux.HandleFunc("POST /api/admin/enterprise/orders/{id}/recognize-usage", s.recognizeEnterpriseOrder)
	mux.HandleFunc("POST /api/admin/tenant-quotas/{tenant_id}", s.upsertTenantQuota)
	mux.HandleFunc("POST /api/admin/disputes", s.createDispute)
	mux.HandleFunc("POST /api/admin/source-trust/{contributor_id}/refresh", s.refreshSourceTrust)
	mux.HandleFunc("POST /api/admin/review-samples/schedule", s.scheduleReviewSamples)
	mux.HandleFunc("POST /api/review-samples/{id}/complete", s.completeReviewSample)
	mux.HandleFunc("POST /api/admin/datasets/{id}/evaluate", s.evaluateDataset)
	mux.HandleFunc("POST /api/admin/reconciliation", s.createReconciliation)
	mux.HandleFunc("POST /api/admin/dsr", s.createDSR)
	mux.HandleFunc("POST /api/admin/dsr/{id}/fulfill", s.fulfillDSR)
	mux.HandleFunc("POST /api/admin/invoices", s.createInvoice)
	mux.HandleFunc("POST /api/admin/invoices/{id}/paid", s.markInvoicePaid)
	mux.HandleFunc("POST /api/admin/sso-providers", s.upsertSSOProvider)
	mux.HandleFunc("POST /api/admin/inboxes", s.createInbox)
	mux.HandleFunc("POST /api/admin/inbound/messages", s.receiveInboundMessage)
	mux.HandleFunc("POST /api/admin/webhook-cases", s.ingestWebhookCase)
	mux.HandleFunc("POST /api/admin/trace-exports", s.ingestTraceExport)
	mux.HandleFunc("POST /api/admin/content-safety/case/{id}/run", s.runContentSafety)
	mux.HandleFunc("GET /api/admin/migrations/status", s.migrationStatus)
	mux.HandleFunc("GET /api/admin/migrations/plan", s.migrationPlan)
	mux.HandleFunc("GET /api/admin/launch-readiness", s.launchReadiness)
	mux.HandleFunc("POST /api/admin/internal-test/bootstrap", s.bootstrapInternalTest)
	mux.HandleFunc("GET /api/admin/operational-alerts", s.operationalAlerts)
	mux.HandleFunc("POST /api/admin/maintenance/run", s.runMaintenance)
	mux.HandleFunc("GET /api/admin/datasets/{id}/commercial-proof", s.commercialProof)
	mux.HandleFunc("POST /api/admin/datasets/{id}/delivery-grants", s.createDeliveryGrant)
	mux.HandleFunc("POST /api/admin/payout-transfers", s.createPayoutTransfer)
	mux.HandleFunc("POST /api/admin/payout-transfers/{id}/confirm", s.confirmPayoutTransfer)
	mux.HandleFunc("POST /api/admin/buyer-usage-reports", s.createBuyerUsageReport)
	mux.HandleFunc("POST /api/admin/payout-profiles/{contributor_id}", s.upsertAdminPayoutProfile)
	mux.HandleFunc("GET /api/enterprise/portal/{id}", s.enterprisePortal)
	mux.HandleFunc("POST /api/enterprise/portal/{id}/usage-reports", s.enterprisePortalUsageReport)
	mux.HandleFunc("POST /api/object-storage/temporary-upload-credentials", s.temporaryUploadCredentials)
	mux.HandleFunc("POST /api/admin/object-storage/temporary-upload-credentials", s.temporaryUploadCredentials)
	mux.HandleFunc("/api/admin/", s.adminFallback)
	mux.HandleFunc("/api/enterprise/", s.enterpriseFallback)
	return s.requestID(s.accessLog(s.recover(s.cors(s.rateLimit(s.limitBody(mux))))))
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "service": "lodia-api-go", "version": "go-mysql-redis-oss"})
}

func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	dbHealth := s.db.Health(ctx)
	redisHealth := s.queue.Health(ctx)
	objectHealth := s.objects.Health(ctx)
	ok := truthy(dbHealth["ok"]) && truthy(redisHealth["ok"]) && truthy(objectHealth["ok"])
	status := http.StatusOK
	if !ok {
		status = http.StatusServiceUnavailable
	}
	writeJSON(w, status, map[string]any{"ok": ok, "mysql": dbHealth, "redis": redisHealth, "object_storage": objectHealth})
}

func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	_ = readJSON(r, &req)
	if strings.TrimSpace(req.Email) != "" && strings.TrimSpace(req.Password) != "" {
		user, err := s.db.AuthenticateUser(r.Context(), req.Email, req.Password, s.cfg.PasswordPepper)
		if err == nil {
			token, rawToken, err := s.db.IssueAuthToken(r.Context(), user.ID, user.ID, 30*24*time.Hour)
			if err != nil {
				writeError(w, http.StatusInternalServerError, err.Error())
				return
			}
			_ = s.db.Audit(r.Context(), user.ID, "auth.login", "user", user.ID, map[string]any{"role": user.Role, "token_id": token.ID})
			writeJSON(w, http.StatusOK, map[string]any{"token": rawToken, "role": user.Role, "user": store.UserPayload(user), "token_id": token.ID})
			return
		}
		if loginError(w, err) {
			return
		}
		if !errors.Is(err, sql.ErrNoRows) {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
	}
	if strings.EqualFold(s.cfg.Env, "production") {
		writeError(w, http.StatusUnauthorized, "invalid_credentials")
		return
	}
	role := "contributor"
	token := s.cfg.ContributorToken
	if strings.Contains(strings.ToLower(req.Email), "admin") {
		role = "admin"
		token = s.cfg.AdminToken
	} else if strings.Contains(strings.ToLower(req.Email), "review") {
		role = "reviewer"
		token = s.cfg.ReviewerToken
	}
	if token == "" && !s.cfg.AuthEnabled() {
		token = "demo-token"
	}
	writeJSON(w, http.StatusOK, map[string]any{"token": token, "role": role})
}

func (s *Server) preview(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "contributor", "reviewer", "admin"); !ok {
		return
	}
	var req struct {
		OwnerID     string   `json:"owner_id"`
		Text        string   `json:"text"`
		AllowedUses []string `json:"allowed_uses"`
	}
	if !decodeOr400(w, r, &req) {
		return
	}
	if strings.TrimSpace(req.Text) == "" {
		writeError(w, http.StatusBadRequest, "text_required")
		return
	}
	preview := s.processor.Preview(req.Text, defaultAllowedUses(req.AllowedUses))
	writeJSON(w, http.StatusOK, map[string]any{"status": "preview_ready", "case": preview})
}

func (s *Server) createTextSubmission(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "contributor", "admin")
	if !ok {
		return
	}
	var req struct {
		OwnerID     string   `json:"owner_id"`
		Text        string   `json:"text"`
		AllowedUses []string `json:"allowed_uses"`
	}
	if !decodeOr400(w, r, &req) {
		return
	}
	if strings.TrimSpace(req.Text) == "" {
		writeError(w, http.StatusBadRequest, "text_required")
		return
	}
	ownerID := s.contributorScope(r, actor, firstNonEmpty(req.OwnerID, "demo_contributor"))
	sub, c, err := s.createSubmissionFromText(r.Context(), ownerID, "text", req.Text, defaultAllowedUses(req.AllowedUses), r.URL.Query().Get("sync") == "1")
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	resp := map[string]any{"submission_id": sub.ID, "status": sub.Status}
	if c != nil {
		resp["case"] = s.caseDTO(*c)
		resp["status"] = "processed"
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) getSubmission(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "contributor", "reviewer", "admin"); !ok {
		return
	}
	id := r.PathValue("id")
	sub, err := s.db.GetSubmission(r.Context(), id)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	var casePtr *caseResponse
	if c, err := s.db.GetCaseBySubmission(r.Context(), id); err == nil {
		dto := s.caseDTO(c)
		casePtr = &dto
	} else if sub.DuplicateOfCaseID != "" {
		if c, err := s.db.GetCase(r.Context(), sub.DuplicateOfCaseID); err == nil {
			dto := s.caseDTO(c)
			casePtr = &dto
		}
	}
	jobs, _ := s.db.JobsBySubmission(r.Context(), id)
	writeJSON(w, http.StatusOK, map[string]any{
		"submission_id": sub.ID,
		"owner_id":      sub.OwnerID,
		"status":        sub.Status,
		"submission":    submissionDTO(sub),
		"case":          casePtr,
		"jobs":          jobsDTO(jobs),
	})
}

func (s *Server) listCases(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "reviewer", "admin"); !ok {
		return
	}
	cases, err := s.db.ListCases(r.Context(), queryLimit(r, 20))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": s.casesDTO(cases)})
}

func (s *Server) reviewQueue(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "reviewer", "admin"); !ok {
		return
	}
	cases, err := s.db.ListReviewQueue(r.Context(), queryLimit(r, 20))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": s.casesDTO(cases)})
}

func (s *Server) claimReview(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "reviewer", "admin")
	if !ok {
		return
	}
	var req struct {
		ReviewerID string `json:"reviewer_id"`
	}
	_ = readJSON(r, &req)
	reviewerID := firstNonEmpty(req.ReviewerID, actor.Subject, "reviewer")
	c, err := s.db.ClaimNextCase(r.Context(), reviewerID)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	_ = s.db.Audit(r.Context(), reviewerID, "review.claimed", "case", c.ID, nil)
	writeJSON(w, http.StatusOK, s.caseDTO(c))
}

func (s *Server) releaseReview(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "reviewer", "admin")
	if !ok {
		return
	}
	id := r.PathValue("id")
	if err := s.db.ReleaseCase(r.Context(), id); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	_ = s.db.Audit(r.Context(), actor.Subject, "review.released", "case", id, nil)
	c, _ := s.db.GetCase(r.Context(), id)
	writeJSON(w, http.StatusOK, s.caseDTO(c))
}

func (s *Server) approveCase(w http.ResponseWriter, r *http.Request) {
	s.reviewDecision(w, r, "approved", "approve")
}

func (s *Server) rejectCase(w http.ResponseWriter, r *http.Request) {
	s.reviewDecision(w, r, "rejected", "reject")
}

func (s *Server) expertVerify(w http.ResponseWriter, r *http.Request) {
	s.reviewDecision(w, r, "review_ready", "expert_verify")
}

func (s *Server) goldReview(w http.ResponseWriter, r *http.Request) {
	s.reviewDecision(w, r, "review_ready", "gold_review")
}

func (s *Server) reviewDecision(w http.ResponseWriter, r *http.Request, status string, reviewType string) {
	actor, ok := s.require(w, r, "reviewer", "admin")
	if !ok {
		return
	}
	var req struct {
		ReviewerID string         `json:"reviewer_id"`
		Notes      string         `json:"notes"`
		Reason     string         `json:"reason"`
		Score      float64        `json:"score"`
		Evidence   map[string]any `json:"evidence"`
		Rubric     map[string]any `json:"rubric"`
	}
	_ = readJSON(r, &req)
	id := r.PathValue("id")
	reviewerID := firstNonEmpty(req.ReviewerID, actor.Subject, "reviewer")
	decision := status
	if status == "review_ready" {
		decision = reviewType
	}
	notes := firstNonEmpty(req.Notes, req.Reason)
	_ = s.db.CreateReview(r.Context(), id, reviewerID, reviewType, decision, req.Score, notes, map[string]any{"evidence": req.Evidence, "rubric": req.Rubric})
	if err := s.db.SetCaseStatus(r.Context(), id, status); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	_ = s.db.Audit(r.Context(), reviewerID, "review."+reviewType, "case", id, map[string]any{"decision": decision})
	c, err := s.db.GetCase(r.Context(), id)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, s.caseDTO(c))
}

func (s *Server) getLongHorizon(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "reviewer", "admin"); !ok {
		return
	}
	c, err := s.db.GetCase(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	wb := workbenchForCase(c)
	fieldQuality := focus.Evaluate(wb.Task, wb.Evidence.SourceChars)
	writeJSON(w, http.StatusOK, longHorizonResponse{s: s, c: c, wb: wb, fq: fieldQuality}.Map())
}

func (s *Server) saveLongHorizon(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "reviewer", "admin")
	if !ok {
		return
	}
	var req struct {
		ReviewerID string              `json:"reviewer_id"`
		Notes      string              `json:"notes"`
		Fields     map[string][]string `json:"fields"`
	}
	if !decodeOr400(w, r, &req) {
		return
	}
	c, err := s.db.GetCase(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	task := focus.EmptyTask()
	for field, values := range req.Fields {
		for _, value := range values {
			clean := redaction.Redact(value).RedactedText
			task[field] = append(task[field], clean)
		}
	}
	prev := workbenchForCase(c)
	reviewerID := firstNonEmpty(req.ReviewerID, actor.Subject, "reviewer")
	wb := focus.BuildWorkbench(task, firstPositive(prev.Evidence.SourceChars, utf8.RuneCountInString(c.RedactedText)), reviewerID, true)
	fq := focus.Evaluate(wb.Task, wb.Evidence.SourceChars)
	ann := annotation.UnmarshalAnnotation(c.AnnotationJSON)
	gate := annotation.UnmarshalGate(c.QualityGateJSON)
	ann, gate = annotation.WithLongHorizonRefinement(ann, gate, wb, fq)
	nextStatus := "review_ready"
	if !fq.Passed {
		nextStatus = "needs_review"
	}
	if err := s.db.UpdateLongHorizon(r.Context(), c.ID, annotation.Marshal(ann), annotation.Marshal(gate), annotation.Marshal(wb), gate.DRL, gate.CommercialReady, nextStatus); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	_ = s.db.CreateReview(r.Context(), c.ID, reviewerID, "field_refinement", wb.Quality.Gate, fq.Score, req.Notes, map[string]any{"field_quality": fq})
	_ = s.db.Audit(r.Context(), reviewerID, "review.long_horizon_refined", "case", c.ID, map[string]any{"score": fq.Score, "passed": fq.Passed})
	c, _ = s.db.GetCase(r.Context(), c.ID)
	writeJSON(w, http.StatusOK, longHorizonResponse{s: s, c: c, wb: wb, fq: fq}.Map())
}

func (s *Server) listDatasets(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "reviewer", "admin"); !ok {
		return
	}
	datasets, err := s.db.ListDatasets(r.Context(), queryLimit(r, 20))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	items := make([]map[string]any, 0, len(datasets))
	for _, dataset := range datasets {
		items = append(items, datasetDTO(dataset, nil))
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": items})
}

func (s *Server) createDataset(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "reviewer", "admin"); !ok {
		return
	}
	var req struct {
		Name              string `json:"name"`
		Purpose           string `json:"purpose"`
		MinDRL            string `json:"min_drl"`
		GrossRevenueCents int64  `json:"gross_revenue_cents"`
		DirectCostCents   int64  `json:"direct_cost_cents"`
	}
	_ = readJSON(r, &req)
	cases, err := s.db.EligibleCases(r.Context(), s.cfg.DatasetMaxCases)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if len(cases) == 0 {
		writeError(w, http.StatusUnprocessableEntity, "no_commercial_ready_cases")
		return
	}
	caseIDs := make([]string, 0, len(cases))
	for _, c := range cases {
		caseIDs = append(caseIDs, c.ID)
	}
	dataset := store.Dataset{
		Name:    firstNonEmpty(req.Name, "Lodia Long Horizon Task Dataset"),
		Purpose: firstNonEmpty(req.Purpose, "commercial_dataset"),
		MinDRL:  firstNonEmpty(req.MinDRL, "DRL3"),
		CaseIDs: caseIDs,
	}
	if err := s.db.CreateDataset(r.Context(), &dataset); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := s.writeDatasetArtifacts(r.Context(), dataset, cases); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	payout, allocations := payoutPlan(req.GrossRevenueCents, req.DirectCostCents, cases)
	netRevenueCents := req.GrossRevenueCents - req.DirectCostCents
	if netRevenueCents < 0 {
		netRevenueCents = 0
	}
	usage := store.UsageEvent{
		ID:                store.NewID("usage"),
		EventType:         "dataset_exported",
		DatasetID:         dataset.ID,
		Status:            "billable",
		GrossRevenueCents: req.GrossRevenueCents,
		DirectCostCents:   req.DirectCostCents,
		NetRevenueCents:   netRevenueCents,
		PayloadJSON:       annotation.Marshal(map[string]any{"case_count": len(cases), "payout": payout}),
	}
	payout["usage_event_id"] = usage.ID
	payoutEvents := make([]store.PayoutEvent, 0, len(allocations))
	for _, allocation := range allocations {
		payoutEvents = append(payoutEvents, store.PayoutEvent{
			UsageEventID:  usage.ID,
			DatasetID:     dataset.ID,
			CaseID:        allocation.CaseID,
			ContributorID: allocation.ContributorID,
			Status:        "pending",
			AmountCents:   allocation.AmountCents,
			Weight:        allocation.Weight,
			PayloadJSON: annotation.Marshal(map[string]any{
				"source":                 "dataset_export",
				"quality_score":          allocation.QualityScore,
				"platform_share_cents":   payout["platform_share_cents"],
				"contributor_pool_cents": payout["contributor_pool_cents"],
			}),
		})
	}
	if err := s.db.CreateUsageEventWithPayouts(r.Context(), &usage, payoutEvents); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	_ = s.db.Audit(r.Context(), "admin", "dataset.created", "dataset", dataset.ID, map[string]any{"case_count": len(cases), "usage_event_id": usage.ID, "payout_event_count": len(allocations)})
	writeJSON(w, http.StatusOK, datasetDTO(dataset, payout))
}

func (s *Server) datasetContract(w http.ResponseWriter, r *http.Request) {
	dataset, err := s.db.GetDataset(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"id":         store.NewID("contract"),
		"dataset_id": dataset.ID,
		"status":     "ready",
		"contract": map[string]any{
			"version":    "lodia-data-contract-v1",
			"purpose":    dataset.Purpose,
			"min_drl":    dataset.MinDRL,
			"case_count": len(dataset.CaseIDs),
		},
	})
}

func (s *Server) datasetArtifact(w http.ResponseWriter, r *http.Request) {
	artifact, err := s.db.GetDatasetArtifact(r.Context(), r.PathValue("id"), r.PathValue("artifact"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	text, err := s.objects.GetText(r.Context(), artifact.ObjectURI)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeText(w, http.StatusOK, artifact.ContentType, text)
}

func (s *Server) auditLogs(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	logs, err := s.db.ListAudit(r.Context(), queryLimit(r, 20))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": logs})
}

func (s *Server) metrics(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	metrics, err := s.db.Metrics(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, metrics)
}

func (s *Server) observability(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	metrics, err := s.db.Metrics(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":                true,
		"case_drl":          metrics["case_drl"],
		"payouts":           metrics["payouts"],
		"payout_batches":    metrics["payout_batches"],
		"reviews":           metrics["reviews"],
		"model_invocations": map[string]int64{"auto_labeler": 0},
		"queue_depth":       metrics["jobs"],
		"limits": map[string]any{
			"max_request_body_bytes": s.cfg.MaxRequestBytes,
			"rate_limit_enabled":     s.cfg.RateLimitEnabled,
			"rate_limit_requests":    s.cfg.RateLimitRequests,
			"rate_limit_window_sec":  int(s.cfg.RateLimitWindow.Seconds()),
			"trust_proxy_headers":    s.cfg.TrustProxyHeaders,
			"access_log_enabled":     s.cfg.AccessLogEnabled,
		},
	})
}

func (s *Server) createAsset(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "contributor", "admin")
	if !ok {
		return
	}
	if err := r.ParseMultipartForm(s.cfg.MaxRequestBytes); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_multipart")
		return
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		writeError(w, http.StatusBadRequest, "file_required")
		return
	}
	defer file.Close()
	body, err := io.ReadAll(io.LimitReader(file, s.cfg.MaxRequestBytes+1))
	if err != nil {
		writeError(w, http.StatusBadRequest, "file_read_failed")
		return
	}
	if int64(len(body)) > s.cfg.MaxRequestBytes {
		writeError(w, http.StatusRequestEntityTooLarge, "file_too_large")
		return
	}
	filename := sanitizeFilename(header.Filename)
	mediaType := header.Header.Get("Content-Type")
	if mediaType == "" {
		mediaType = http.DetectContentType(body)
	}
	ownerID := s.contributorScope(r, actor, firstNonEmpty(r.FormValue("owner_id"), "demo_contributor"))
	assetID := store.NewID("asset")
	uri, err := s.objects.Put(r.Context(), objectKey("assets", assetID, filename), body, mediaType)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	asset := store.Asset{
		ID:        assetID,
		OwnerID:   ownerID,
		Filename:  filename,
		MediaType: mediaType,
		AssetType: assetType(mediaType, filename),
		ByteSize:  int64(len(body)),
		ObjectURI: uri,
		Status:    "stored",
	}
	if err := s.db.CreateAsset(r.Context(), &asset); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	_ = s.db.Audit(r.Context(), ownerID, "asset.stored", "asset", asset.ID, map[string]any{"media_type": mediaType, "byte_size": len(body)})
	writeJSON(w, http.StatusOK, map[string]any{"asset": assetDTO(asset)})
}

func (s *Server) listAssets(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "contributor", "reviewer", "admin"); !ok {
		return
	}
	assets, err := s.db.ListAssets(r.Context(), queryLimit(r, 20))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	items := make([]map[string]any, 0, len(assets))
	for _, asset := range assets {
		items = append(items, assetDTO(asset))
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": items})
}

func (s *Server) extractAsset(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "contributor", "admin")
	if !ok {
		return
	}
	asset, err := s.db.GetAsset(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	if !isTextAsset(asset.MediaType, asset.Filename) {
		_ = s.db.UpdateAssetExtraction(r.Context(), asset.ID, "awaiting_multimodal_extractor", "")
		writeJSON(w, http.StatusAccepted, map[string]any{"asset": assetDTO(asset), "status": "awaiting_multimodal_extractor"})
		return
	}
	text, err := s.objects.GetText(r.Context(), asset.ObjectURI)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	sub, c, err := s.createSubmissionFromText(r.Context(), firstNonEmpty(asset.OwnerID, actor.Subject), "asset_text", text, defaultAllowedUses(nil), false)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	_ = s.db.UpdateAssetExtraction(r.Context(), asset.ID, "extraction_queued", sub.ID)
	resp := map[string]any{"asset": assetDTO(asset), "submission_id": sub.ID, "status": sub.Status}
	if c != nil {
		resp["case"] = s.caseDTO(*c)
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) authorizations(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "contributor", "reviewer", "admin")
	if !ok {
		return
	}
	contributorID := s.contributorScope(r, actor, "demo_contributor")
	authID := authorizationIDForOwner(contributorID)
	status := "active"
	withdrawalCount := int64(0)
	if withdrawals, err := s.db.CountAuthorizationWithdrawals(r.Context(), authID); err == nil && withdrawals > 0 {
		status = "withdrawn"
		withdrawalCount = withdrawals
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": []map[string]any{{
		"id":               authID,
		"owner_id":         contributorID,
		"status":           status,
		"withdrawal_count": withdrawalCount,
		"allowed_uses":     defaultAllowedUses(nil),
		"policy_version":   "cn-independent-v1",
		"terms_version":    "lodia-v1",
	}}})
}

func (s *Server) contributorDashboard(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "contributor", "reviewer", "admin")
	if !ok {
		return
	}
	contributorID := s.contributorScope(r, actor, "demo_contributor")
	recentCases, _ := s.db.ListCasesByOwner(r.Context(), contributorID, 6)
	allCases, _ := s.db.ListCasesByOwner(r.Context(), contributorID, 500)
	caseStatus, _ := s.db.CaseStatusCountsByOwner(r.Context(), contributorID)
	caseDRL, _ := s.db.CaseDRLCountsByOwner(r.Context(), contributorID)
	assetStatus, _ := s.db.AssetStatusCountsByOwner(r.Context(), contributorID)
	assetCount, _ := s.db.CountAssetsByOwner(r.Context(), contributorID, "")
	profileStatus := "missing"
	if profile, err := s.db.LatestPayoutProfileByContributor(r.Context(), contributorID); err == nil {
		profileStatus = profile.Status
	}
	ledger, err := s.db.ContributorLedgerSummary(r.Context(), contributorID)
	if err != nil {
		ledger = map[string]any{"pending_cents": 0, "batched_cents": 0, "settled_cents": 0, "total_cents": 0, "payout_count": 0, "by_status": map[string]int64{}}
	}
	trust := sourceTrustFromCases(contributorID, allCases)
	writeJSON(w, http.StatusOK, map[string]any{
		"contributor_id": contributorID,
		"cases": map[string]any{
			"total":     sumCounts(caseStatus),
			"by_status": caseStatus,
			"by_drl":    caseDRL,
			"recent":    s.casesDTO(recentCases),
		},
		"assets":                map[string]any{"total": assetCount, "by_status": assetStatus},
		"ledger":                ledger,
		"source_trust":          trust,
		"payout_profile_status": profileStatus,
	})
}

func (s *Server) contributorOnboarding(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "contributor", "reviewer", "admin")
	if !ok {
		return
	}
	contributorID := s.contributorScope(r, actor, "demo_contributor")
	cases, _ := s.db.ListCasesByOwner(r.Context(), contributorID, 100)
	readyCases, _ := s.db.CountCommercialReadyCasesByOwner(r.Context(), contributorID)
	inboxCount, _ := s.db.CountInboxesByOwner(r.Context(), contributorID, "active")
	profileStatus := "missing"
	if profile, err := s.db.LatestPayoutProfileByContributor(r.Context(), contributorID); err == nil {
		profileStatus = profile.Status
	}
	authID := authorizationIDForOwner(contributorID)
	withdrawalCount, _ := s.db.CountAuthorizationWithdrawals(r.Context(), authID)
	activeAuthorizationCount := int64(1)
	if withdrawalCount > 0 {
		activeAuthorizationCount = 0
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"contributor_id": contributorID,
		"ready":          readyCases > 0 && profileStatus == "active" && activeAuthorizationCount > 0,
		"next_actions":   []string{"submit_long_horizon_case", "complete_field_review", "submit_payout_profile"},
		"signals": map[string]any{
			"case_count":                 len(cases),
			"commercial_ready_count":     readyCases,
			"active_inbox_count":         inboxCount,
			"active_authorization_count": activeAuthorizationCount,
			"authorization_id":           authID,
			"payout_profile_status":      profileStatus,
		},
	})
}

type traceExportRequest struct {
	OwnerID             string                  `json:"owner_id"`
	Source              string                  `json:"source"`
	ExternalID          string                  `json:"external_id"`
	Title               string                  `json:"title"`
	Text                string                  `json:"text"`
	AllowedUses         []string                `json:"allowed_uses"`
	Trace               map[string]any          `json:"trace"`
	EvidenceAttachments []traceExportAttachment `json:"evidence_attachments"`
	Sync                bool                    `json:"sync"`
}

type traceExportAttachment struct {
	Filename      string `json:"filename"`
	MediaType     string `json:"media_type"`
	ContentBase64 string `json:"content_base64"`
}

func (s *Server) ingestTraceExport(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "contributor", "admin")
	if !ok {
		return
	}
	var req traceExportRequest
	if !decodeOr400(w, r, &req) {
		return
	}
	ownerID := s.contributorScope(r, actor, firstNonEmpty(req.OwnerID, "demo_contributor"))
	text := firstNonEmpty(req.Text, traceExportText(req))
	if strings.TrimSpace(text) == "" {
		writeError(w, http.StatusBadRequest, "trace_or_text_required")
		return
	}
	decodedAttachments := make([]struct {
		filename  string
		mediaType string
		body      []byte
	}, 0, len(req.EvidenceAttachments))
	totalAttachmentBytes := int64(0)
	for _, attachment := range req.EvidenceAttachments {
		if strings.TrimSpace(attachment.ContentBase64) == "" {
			continue
		}
		body, err := base64.StdEncoding.DecodeString(attachment.ContentBase64)
		if err != nil {
			writeError(w, http.StatusBadRequest, "invalid_attachment_base64")
			return
		}
		totalAttachmentBytes += int64(len(body))
		if totalAttachmentBytes > s.cfg.MaxRequestBytes {
			writeError(w, http.StatusRequestEntityTooLarge, "attachments_too_large")
			return
		}
		filename := sanitizeFilename(firstNonEmpty(attachment.Filename, "trace-evidence.txt"))
		mediaType := firstNonEmpty(attachment.MediaType, http.DetectContentType(body))
		decodedAttachments = append(decodedAttachments, struct {
			filename  string
			mediaType string
			body      []byte
		}{filename: filename, mediaType: mediaType, body: body})
	}
	sub, c, err := s.createSubmissionFromText(r.Context(), ownerID, "trace_export", text, defaultAllowedUses(req.AllowedUses), req.Sync || r.URL.Query().Get("sync") == "1")
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	assets := make([]map[string]any, 0, len(decodedAttachments))
	for _, attachment := range decodedAttachments {
		assetID := store.NewID("asset")
		uri, err := s.objects.Put(r.Context(), objectKey("trace-evidence", assetID, attachment.filename), attachment.body, attachment.mediaType)
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		asset := store.Asset{
			ID:           assetID,
			OwnerID:      ownerID,
			SubmissionID: sub.ID,
			Filename:     attachment.filename,
			MediaType:    attachment.mediaType,
			AssetType:    assetType(attachment.mediaType, attachment.filename),
			ByteSize:     int64(len(attachment.body)),
			ObjectURI:    uri,
			Status:       "stored",
		}
		if err := s.db.CreateAsset(r.Context(), &asset); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		assets = append(assets, assetDTO(asset))
	}
	traceID := "trace_" + shortHash(firstNonEmpty(req.ExternalID, req.Title, sub.ID))
	_ = s.db.Audit(r.Context(), ownerID, "trace_export.ingested", "submission", sub.ID, map[string]any{"trace_export_id": traceID, "source": req.Source, "external_id_hash": optionalHash(req.ExternalID), "attachment_count": len(assets)})
	resp := map[string]any{
		"trace_export_id": traceID,
		"owner_id":        ownerID,
		"submission_id":   sub.ID,
		"status":          sub.Status,
		"assets":          assets,
		"source":          firstNonEmpty(req.Source, "manual_trace_export"),
	}
	if c != nil {
		resp["case"] = s.caseDTO(*c)
		resp["status"] = "processed"
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) enterpriseSamplePacks(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"schema": "lodia-enterprise-sample-pack-v1",
		"items": []map[string]any{
			enterpriseSamplePack("code_fix", "代码修复 Agent 任务", "定位缺陷、修改代码、运行测试、复盘修复证据", []string{"failing_test", "diff_summary", "verification_commands"}),
			enterpriseSamplePack("agent_tool_use", "Agent 工具调用任务", "多工具链调用、错误恢复、权限边界和执行记录", []string{"tool_sequence", "tool_outputs", "retry_path"}),
			enterpriseSamplePack("business_workflow", "企业流程执行任务", "跨系统流程、审批约束、产出物和可审计完成标准", []string{"workflow_steps", "system_records", "approval_evidence"}),
			enterpriseSamplePack("model_eval_review", "模型评测复盘任务", "评测目标、样本缺陷、裁决理由和改进建议", []string{"eval_metric", "failure_cluster", "judge_rationale"}),
		},
	})
}

func (s *Server) temporaryUploadCredentials(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "contributor", "admin"); !ok {
		return
	}
	var req struct {
		KeyPrefix        string `json:"key_prefix"`
		ExpiresInSeconds int    `json:"expires_in_seconds"`
	}
	if r.Body != nil && r.ContentLength != 0 {
		if !decodeOr400(w, r, &req) {
			return
		}
	}
	creds, err := objectstore.TemporaryUploadCredentials(r.Context(), s.cfg, req.KeyPrefix, req.ExpiresInSeconds)
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, creds)
}

func (s *Server) adminFallback(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/admin/")
	writeError(w, http.StatusNotFound, "admin_route_not_found:"+path)
}

func (s *Server) enterpriseFallback(w http.ResponseWriter, r *http.Request) {
	writeError(w, http.StatusNotFound, "enterprise_route_not_found:"+strings.TrimPrefix(r.URL.Path, "/api/enterprise/"))
}

func (s *Server) createSubmissionFromText(ctx context.Context, ownerID string, sourceType string, text string, allowedUses []string, forceSync bool) (store.Submission, *store.Case, error) {
	subID := store.NewID("sub")
	rawHash := redaction.HashRaw(text)
	uri, err := s.objects.PutText(ctx, objectKey("raw", sourceType, subID+".txt"), text, "text/plain; charset=utf-8")
	if err != nil {
		return store.Submission{}, nil, err
	}
	sub := store.Submission{
		ID:           subID,
		OwnerID:      ownerID,
		SourceType:   sourceType,
		Status:       "queued",
		RawObjectURI: uri,
		RawHash:      rawHash,
		AllowedUses:  defaultAllowedUses(allowedUses),
		RawExpiresAt: rawExpiry(s.cfg.RawObjectTTL),
	}
	if err := s.db.CreateSubmission(ctx, &sub); err != nil {
		return store.Submission{}, nil, err
	}
	payload := annotation.Marshal(map[string]string{"submission_id": sub.ID})
	job := store.Job{SubmissionID: sub.ID, QueueName: s.cfg.WorkerQueue, JobType: "process_submission", Status: "queued", PayloadJSON: payload, MaxAttempts: 5}
	if err := s.db.CreateJob(ctx, &job); err != nil {
		return store.Submission{}, nil, err
	}
	_ = s.db.Audit(ctx, ownerID, "submission.created", "submission", sub.ID, map[string]any{"source_type": sourceType})
	if forceSync || !s.cfg.AsyncProcessing {
		c, err := s.processor.ProcessSubmission(ctx, sub.ID)
		if err != nil {
			return sub, nil, err
		}
		_ = s.db.MarkJobDone(ctx, job.ID)
		return sub, &c, nil
	}
	if err := s.queue.Push(ctx, s.cfg.WorkerQueue, job.ID); err != nil {
		_, _ = s.db.MarkJobFailed(ctx, job.ID, err.Error())
		return sub, nil, err
	}
	return sub, nil, nil
}

func (s *Server) writeDatasetArtifacts(ctx context.Context, dataset store.Dataset, cases []store.Case) error {
	dataLines := strings.Builder{}
	totalScore := 0.0
	for _, c := range cases {
		ann := annotation.UnmarshalAnnotation(c.AnnotationJSON)
		gate := annotation.UnmarshalGate(c.QualityGateJSON)
		totalScore += ann.QualityScore
		line := map[string]any{
			"case_id":           c.ID,
			"owner_id":          c.OwnerID,
			"redacted_text":     c.RedactedText,
			"annotation":        ann,
			"quality_gate":      gate,
			"long_horizon_task": annotation.UnmarshalWorkbench(c.LongHorizonJSON),
			"license":           map[string]any{"allowed_uses": gate.AllowedUses, "commercial_ready": gate.CommercialReady},
		}
		dataLines.WriteString(annotation.Marshal(line))
		dataLines.WriteByte('\n')
	}
	avg := 0.0
	if len(cases) > 0 {
		avg = totalScore / float64(len(cases))
	}
	artifacts := map[string]string{
		"data": dataLines.String(),
		"manifest": annotation.Marshal(map[string]any{
			"dataset_id":   dataset.ID,
			"name":         dataset.Name,
			"case_ids":     dataset.CaseIDs,
			"case_count":   len(cases),
			"generated_at": time.Now().UTC(),
			"storage":      s.cfg.ObjectBackend,
		}),
		"quality_report": annotation.Marshal(map[string]any{
			"dataset_id":             dataset.ID,
			"case_count":             len(cases),
			"average_quality_score":  avg,
			"minimum_drl":            dataset.MinDRL,
			"privacy_status":         "redacted_only",
			"human_review_required":  true,
			"commercial_ready_cases": len(cases),
		}),
		"data_contract": annotation.Marshal(map[string]any{
			"version":    "lodia-data-contract-v1",
			"dataset_id": dataset.ID,
			"purpose":    dataset.Purpose,
			"min_drl":    dataset.MinDRL,
			"case_count": len(cases),
		}),
	}
	for artifactType, content := range artifacts {
		contentType := "application/json; charset=utf-8"
		ext := ".json"
		if artifactType == "data" {
			contentType = "application/x-ndjson; charset=utf-8"
			ext = ".jsonl"
		}
		uri, err := s.objects.PutText(ctx, objectKey("datasets", dataset.ID, artifactType+ext), content, contentType)
		if err != nil {
			return err
		}
		if err := s.db.CreateDatasetArtifact(ctx, &store.DatasetArtifact{DatasetID: dataset.ID, ArtifactType: artifactType, ObjectURI: uri, ContentType: contentType, ByteSize: int64(len(content))}); err != nil {
			return err
		}
	}
	return nil
}

type caseResponse struct {
	CaseID                  string                 `json:"case_id"`
	OwnerID                 string                 `json:"owner_id"`
	Status                  string                 `json:"status"`
	RedactedText            string                 `json:"redacted_text"`
	AuthorizationSnapshotID *string                `json:"authorization_snapshot_id,omitempty"`
	ReviewClaimedBy         *string                `json:"review_claimed_by,omitempty"`
	ReviewClaimedAt         *time.Time             `json:"review_claimed_at,omitempty"`
	CreatedAt               time.Time              `json:"created_at"`
	UpdatedAt               time.Time              `json:"updated_at"`
	Annotation              annotation.Annotation  `json:"annotation"`
	QualityGate             annotation.QualityGate `json:"quality_gate"`
}

func (s *Server) caseDTO(c store.Case) caseResponse {
	var claimedBy *string
	if c.ReviewClaimedBy != "" {
		claimedBy = &c.ReviewClaimedBy
	}
	return caseResponse{
		CaseID:          c.ID,
		OwnerID:         c.OwnerID,
		Status:          c.Status,
		RedactedText:    c.RedactedText,
		ReviewClaimedBy: claimedBy,
		ReviewClaimedAt: c.ReviewClaimedAt,
		CreatedAt:       c.CreatedAt,
		UpdatedAt:       c.UpdatedAt,
		Annotation:      annotation.UnmarshalAnnotation(c.AnnotationJSON),
		QualityGate:     annotation.UnmarshalGate(c.QualityGateJSON),
	}
}

func (s *Server) casesDTO(cases []store.Case) []caseResponse {
	out := make([]caseResponse, 0, len(cases))
	for _, c := range cases {
		out = append(out, s.caseDTO(c))
	}
	return out
}

type longHorizonResponse struct {
	s  *Server
	c  store.Case
	wb focus.Workbench
	fq focus.FieldQuality
}

func (resp longHorizonResponse) Map() map[string]any {
	return map[string]any{
		"case":              resp.s.caseDTO(resp.c),
		"schema":            focus.SchemaVersion,
		"long_horizon_task": resp.wb,
		"fields":            resp.wb.Task,
		"field_quality":     resp.fq,
		"missing":           resp.fq.Missing,
		"required_actions":  annotation.UnmarshalGate(resp.c.QualityGateJSON).RequiredActions,
		"review_claimed_by": nilIfEmpty(resp.c.ReviewClaimedBy),
	}
}

func workbenchForCase(c store.Case) focus.Workbench {
	if strings.TrimSpace(c.LongHorizonJSON) != "" {
		wb := annotation.UnmarshalWorkbench(c.LongHorizonJSON)
		if len(wb.Task) > 0 {
			return wb
		}
	}
	return focus.Extract(c.RedactedText)
}

func submissionDTO(sub store.Submission) map[string]any {
	return map[string]any{
		"id":             sub.ID,
		"owner_id":       sub.OwnerID,
		"source_type":    sub.SourceType,
		"status":         sub.Status,
		"allowed_uses":   sub.AllowedUses,
		"raw_expires_at": sub.RawExpiresAt,
		"raw_deleted_at": sub.RawDeletedAt,
		"created_at":     sub.CreatedAt,
	}
}

func jobsDTO(jobs []store.Job) []map[string]any {
	out := make([]map[string]any, 0, len(jobs))
	for _, job := range jobs {
		out = append(out, map[string]any{
			"id":         job.ID,
			"queue_name": job.QueueName,
			"job_type":   job.JobType,
			"status":     job.Status,
			"attempts":   job.Attempts,
			"error":      job.Error,
			"created_at": job.CreatedAt,
			"updated_at": job.UpdatedAt,
		})
	}
	return out
}

func assetDTO(asset store.Asset) map[string]any {
	return map[string]any{
		"id":                        asset.ID,
		"owner_id":                  asset.OwnerID,
		"submission_id":             nilIfEmpty(asset.SubmissionID),
		"authorization_snapshot_id": nilIfEmpty(asset.AuthorizationSnapshotID),
		"filename":                  asset.Filename,
		"media_type":                asset.MediaType,
		"asset_type":                asset.AssetType,
		"byte_size":                 asset.ByteSize,
		"status":                    asset.Status,
	}
}

func datasetDTO(dataset store.Dataset, payout map[string]any) map[string]any {
	out := map[string]any{"id": dataset.ID, "name": dataset.Name, "status": dataset.Status, "case_ids": dataset.CaseIDs}
	if payout != nil {
		out["payout"] = payout
	}
	return out
}

type payoutAllocation struct {
	ContributorID string  `json:"contributor_id"`
	CaseID        string  `json:"case_id"`
	AmountCents   int64   `json:"amount_cents"`
	Weight        float64 `json:"weight"`
	QualityScore  float64 `json:"quality_score"`
}

func payoutPlan(grossRevenueCents int64, directCostCents int64, cases []store.Case) (map[string]any, []payoutAllocation) {
	net := grossRevenueCents - directCostCents
	if net < 0 {
		net = 0
	}
	platformShare := net / 5
	contributorPool := net - platformShare
	allocations := make([]payoutAllocation, 0, len(cases))
	if len(cases) == 0 || contributorPool <= 0 {
		return map[string]any{"contributor_pool_cents": contributorPool, "platform_share_cents": platformShare, "allocations": allocations}, allocations
	}
	type weightedCase struct {
		index        int
		caseData     store.Case
		qualityScore float64
		weight       float64
		amount       int64
		remainder    float64
	}
	weighted := make([]weightedCase, 0, len(cases))
	totalWeight := 0.0
	for i, c := range cases {
		ann := annotation.UnmarshalAnnotation(c.AnnotationJSON)
		weight := ann.QualityScore
		if weight <= 0 {
			weight = 0.1
		}
		weighted = append(weighted, weightedCase{index: i, caseData: c, qualityScore: ann.QualityScore, weight: weight})
		totalWeight += weight
	}
	if totalWeight <= 0 {
		totalWeight = float64(len(weighted))
		for i := range weighted {
			weighted[i].weight = 1
		}
	}
	allocated := int64(0)
	for i := range weighted {
		rawAmount := float64(contributorPool) * weighted[i].weight / totalWeight
		weighted[i].amount = int64(rawAmount)
		weighted[i].remainder = rawAmount - float64(weighted[i].amount)
		allocated += weighted[i].amount
	}
	remaining := contributorPool - allocated
	sort.SliceStable(weighted, func(i, j int) bool {
		if weighted[i].remainder == weighted[j].remainder {
			return weighted[i].index < weighted[j].index
		}
		return weighted[i].remainder > weighted[j].remainder
	})
	for i := 0; i < len(weighted) && remaining > 0; i++ {
		weighted[i].amount++
		remaining--
	}
	sort.SliceStable(weighted, func(i, j int) bool { return weighted[i].index < weighted[j].index })
	for _, item := range weighted {
		allocations = append(allocations, payoutAllocation{
			ContributorID: firstNonEmpty(item.caseData.OwnerID, "demo_contributor"),
			CaseID:        item.caseData.ID,
			AmountCents:   item.amount,
			Weight:        item.weight,
			QualityScore:  item.qualityScore,
		})
	}
	return map[string]any{"contributor_pool_cents": contributorPool, "platform_share_cents": platformShare, "allocations": allocations}, allocations
}

func (s *Server) contributorScope(r *http.Request, actor auth.Context, fallback string) string {
	queryContributor := strings.TrimSpace(r.URL.Query().Get("contributor_id"))
	if actor.Roles["admin"] || actor.Roles["reviewer"] {
		return firstNonEmpty(queryContributor, fallback, actor.Subject, "demo_contributor")
	}
	if actor.Subject == "" || actor.Subject == "demo" {
		return firstNonEmpty(queryContributor, fallback, "demo_contributor")
	}
	return actor.Subject
}

func authorizationIDForOwner(ownerID string) string {
	ownerID = strings.TrimSpace(ownerID)
	if ownerID == "" || ownerID == "demo_contributor" {
		return "auth_demo"
	}
	return "auth_" + shortHash(ownerID)
}

func sourceTrustFromCases(contributorID string, cases []store.Case) map[string]any {
	caseCount := len(cases)
	accepted := 0
	rejected := 0
	for _, c := range cases {
		if c.Status == "approved" || c.CommercialReady {
			accepted++
		}
		if c.Status == "rejected" {
			rejected++
		}
	}
	score := 0.6
	if caseCount > 0 {
		score = 0.55 + 0.4*float64(accepted)/float64(caseCount) - 0.2*float64(rejected)/float64(caseCount)
	}
	if score < 0.1 {
		score = 0.1
	}
	if score > 0.99 {
		score = 0.99
	}
	return map[string]any{"contributor_id": contributorID, "score": round2(score), "case_count": caseCount, "accepted_count": accepted, "rejected_count": rejected, "duplicate_count": 0}
}

func sumCounts(values map[string]int64) int64 {
	total := int64(0)
	for _, value := range values {
		total += value
	}
	return total
}

func traceExportText(req traceExportRequest) string {
	builder := strings.Builder{}
	if strings.TrimSpace(req.Title) != "" {
		builder.WriteString("Title: ")
		builder.WriteString(strings.TrimSpace(req.Title))
		builder.WriteString("\n")
	}
	if strings.TrimSpace(req.Source) != "" {
		builder.WriteString("Source: ")
		builder.WriteString(strings.TrimSpace(req.Source))
		builder.WriteString("\n")
	}
	labels := map[string]string{
		"objective":      "Goal",
		"context":        "Context",
		"constraints":    "Constraints",
		"steps":          "Plan",
		"tool_results":   "Tool results",
		"failures":       "Failures",
		"corrections":    "Corrections",
		"acceptance":     "Acceptance",
		"reusable_rules": "Reusable rule",
	}
	for _, field := range focus.FieldNames {
		values := traceFieldValues(req.Trace[field])
		if len(values) == 0 {
			continue
		}
		builder.WriteString(labels[field])
		builder.WriteString(": ")
		for i, value := range values {
			if i > 0 {
				builder.WriteString("; ")
			}
			builder.WriteString(value)
		}
		builder.WriteString("\n")
	}
	return strings.TrimSpace(builder.String())
}

func traceFieldValues(value any) []string {
	switch v := value.(type) {
	case string:
		if strings.TrimSpace(v) == "" {
			return nil
		}
		return []string{strings.TrimSpace(v)}
	case []string:
		out := make([]string, 0, len(v))
		for _, item := range v {
			if strings.TrimSpace(item) != "" {
				out = append(out, strings.TrimSpace(item))
			}
		}
		return out
	case []any:
		out := make([]string, 0, len(v))
		for _, item := range v {
			if text, ok := item.(string); ok && strings.TrimSpace(text) != "" {
				out = append(out, strings.TrimSpace(text))
			}
		}
		return out
	default:
		return nil
	}
}

func enterpriseSamplePack(taskType string, title string, buyerValue string, evidenceFields []string) map[string]any {
	return map[string]any{
		"task_type":        taskType,
		"title":            title,
		"buyer_value":      buyerValue,
		"required_fields":  focus.RequiredFields,
		"evidence_fields":  evidenceFields,
		"quality_floor":    map[string]any{"drl": "DRL3", "long_horizon_score": 0.78, "content_safety": "low_risk_allow"},
		"commercial_scope": []string{"training", "evaluation", "agent_workflow_analysis"},
	}
}

func (s *Server) require(w http.ResponseWriter, r *http.Request, roles ...string) (auth.Context, bool) {
	ctx := auth.FromRequest(r, s.cfg)
	if ctx.HasAny(roles...) {
		return ctx, true
	}
	if token := auth.BearerToken(r.Header.Get("Authorization")); token != "" {
		if principal, err := s.db.LookupToken(r.Context(), token); err == nil {
			ctx = auth.Context{Subject: principal.UserID, Enabled: true, Roles: rolesFor(principal.Role)}
			if ctx.HasAny(roles...) {
				return ctx, true
			}
			writeError(w, http.StatusForbidden, "forbidden")
			return ctx, false
		}
	}
	if ctx.Enabled && ctx.Subject == "" {
		writeError(w, http.StatusUnauthorized, "auth_required")
		return ctx, false
	}
	writeError(w, http.StatusForbidden, "forbidden")
	return ctx, false
}

func rolesFor(role string) map[string]bool {
	switch role {
	case "admin":
		return map[string]bool{"admin": true, "reviewer": true, "contributor": true}
	case "reviewer":
		return map[string]bool{"reviewer": true}
	case "contributor":
		return map[string]bool{"contributor": true}
	default:
		return map[string]bool{}
	}
}

func decodeOr400(w http.ResponseWriter, r *http.Request, dst any) bool {
	if err := readJSON(r, dst); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json")
		return false
	}
	return true
}

func readJSON(r *http.Request, dst any) error {
	if r.Body == nil {
		return nil
	}
	defer r.Body.Close()
	decoder := json.NewDecoder(r.Body)
	return decoder.Decode(dst)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeText(w http.ResponseWriter, status int, contentType string, value string) {
	if contentType == "" {
		contentType = "text/plain; charset=utf-8"
	}
	w.Header().Set("Content-Type", contentType)
	w.WriteHeader(status)
	_, _ = w.Write([]byte(value))
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]any{"error": message})
}

func writeStoreError(w http.ResponseWriter, err error) {
	if errors.Is(err, sql.ErrNoRows) {
		writeError(w, http.StatusNotFound, "not_found")
		return
	}
	writeError(w, http.StatusInternalServerError, err.Error())
}

func (s *Server) limitBody(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Body != nil {
			r.Body = http.MaxBytesReader(w, r.Body, s.cfg.MaxRequestBytes)
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) recover(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if recovered := recover(); recovered != nil {
				logRequestPanic(r, recovered)
				writeError(w, http.StatusInternalServerError, "internal_error")
			}
		}()
		next.ServeHTTP(w, r)
	})
}

func (s *Server) cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if allowedOrigin := s.allowedOrigin(origin); allowedOrigin != "" {
			w.Header().Set("Access-Control-Allow-Origin", allowedOrigin)
			w.Header().Set("Vary", "Origin")
		}
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) allowedOrigin(origin string) string {
	if len(s.cfg.AllowedOrigins) == 0 {
		return "*"
	}
	for _, allowed := range s.cfg.AllowedOrigins {
		if allowed == origin {
			return origin
		}
	}
	return ""
}

func queryLimit(r *http.Request, fallback int) int {
	value, err := strconv.Atoi(r.URL.Query().Get("limit"))
	if err != nil || value <= 0 {
		return fallback
	}
	if value > 100 {
		return 100
	}
	return value
}

func truthy(value any) bool {
	v, ok := value.(bool)
	return ok && v
}

func defaultAllowedUses(values []string) []string {
	if len(values) > 0 {
		return values
	}
	return []string{"private_library", "candidate_pool", "commercial_dataset", "training"}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func firstPositive(values ...int) int {
	for _, value := range values {
		if value > 0 {
			return value
		}
	}
	return 0
}

func nilIfEmpty(value string) any {
	if value == "" {
		return nil
	}
	return value
}

func sanitizeFilename(value string) string {
	base := filepath.Base(value)
	base = strings.ReplaceAll(base, "\\", "_")
	base = strings.TrimSpace(base)
	if base == "" || base == "." || base == "/" {
		return "upload.bin"
	}
	return base
}

func assetType(mediaType string, filename string) string {
	if strings.HasPrefix(mediaType, "image/") {
		return "image"
	}
	if strings.HasPrefix(mediaType, "audio/") {
		return "audio"
	}
	if strings.HasPrefix(mediaType, "video/") {
		return "video"
	}
	if isTextAsset(mediaType, filename) {
		return "text"
	}
	return "file"
}

func isTextAsset(mediaType string, filename string) bool {
	if strings.HasPrefix(mediaType, "text/") || strings.Contains(mediaType, "json") || strings.Contains(mediaType, "xml") {
		return true
	}
	ext := strings.ToLower(filepath.Ext(filename))
	return ext == ".txt" || ext == ".md" || ext == ".json" || ext == ".jsonl" || ext == ".csv"
}
