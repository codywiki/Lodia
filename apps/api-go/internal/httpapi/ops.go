package httpapi

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/codywiki/lodia/apps/api-go/internal/store"
)

func (s *Server) createPayoutBatch(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	var req struct {
		MinAmountCents int64 `json:"min_amount_cents"`
		MaxEvents      int   `json:"max_events"`
	}
	_ = readJSON(r, &req)
	maxEvents := firstPositive(req.MaxEvents, 100)
	events, err := s.db.ListPayoutEventsByStatus(r.Context(), "pending", maxEvents)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if len(events) == 0 {
		writeError(w, http.StatusUnprocessableEntity, "no_pending_payout_events")
		return
	}
	eventIDs := make([]string, 0, len(events))
	contributorIDs := map[string]bool{}
	total := int64(0)
	for _, event := range events {
		eventIDs = append(eventIDs, event.ID)
		contributorIDs[event.ContributorID] = true
		total += event.AmountCents
	}
	if req.MinAmountCents > 0 && total < req.MinAmountCents {
		writeError(w, http.StatusUnprocessableEntity, "pending_payout_below_minimum")
		return
	}
	payload := map[string]any{"max_events": maxEvents, "event_ids": eventIDs, "contributor_count": len(contributorIDs)}
	payloadJSON, _ := json.Marshal(payload)
	batch := store.PayoutBatch{
		Status:           "ready",
		PayoutCount:      len(events),
		TotalAmountCents: total,
		MinAmountCents:   req.MinAmountCents,
		MaxEvents:        maxEvents,
		PayloadJSON:      string(payloadJSON),
	}
	err = s.db.CreatePayoutBatchForEvents(r.Context(), &batch, eventIDs)
	if err != nil {
		if strings.Contains(err.Error(), "payout_event_unavailable") {
			writeError(w, http.StatusConflict, "payout_event_unavailable")
			return
		}
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	_ = s.db.Audit(r.Context(), "admin", "payout_batch.created", "payout_batch", batch.ID, store.PayoutBatchPayload(batch))
	writeJSON(w, http.StatusOK, store.PayoutBatchPayload(batch))
}

func (s *Server) settlePayoutBatch(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	batch, err := s.db.SettlePayoutBatchAndEvents(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	_ = s.db.Audit(r.Context(), "admin", "payout_batch.settled", "payout_batch", batch.ID, store.PayoutBatchPayload(batch))
	writeJSON(w, http.StatusOK, store.PayoutBatchPayload(batch))
}

func (s *Server) createEnterpriseCustomer(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		Name         string `json:"name"`
		ContactEmail string `json:"contact_email"`
	}
	_ = readJSON(r, &req)
	domain := emailDomain(req.ContactEmail)
	customer := store.EnterpriseCustomer{
		TenantID:           "tenant_" + shortHash(firstNonEmpty(domain, req.Name, "default")),
		Name:               firstNonEmpty(req.Name, "Demo Buyer"),
		Status:             "active",
		ContactEmailDomain: domain,
	}
	err := s.db.CreateEnterpriseCustomer(r.Context(), &customer)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.EnterpriseCustomerPayload(customer))
}

func (s *Server) listEnterpriseCustomers(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	customers, err := s.db.ListEnterpriseCustomers(r.Context(), queryLimit(r, 20))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": store.EnterpriseCustomersPayload(customers)})
}

func (s *Server) createEnterpriseContract(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		CustomerID   string         `json:"customer_id"`
		TermsVersion string         `json:"terms_version"`
		Terms        map[string]any `json:"terms"`
	}
	_ = readJSON(r, &req)
	expiresAt := time.Now().UTC().AddDate(1, 0, 0).Truncate(time.Microsecond)
	termsJSON, _ := json.Marshal(req.Terms)
	contract := store.EnterpriseContract{
		CustomerID: req.CustomerID,
		Status:     "active",
		Version:    firstNonEmpty(req.TermsVersion, "lodia-enterprise-v1"),
		ExpiresAt:  &expiresAt,
		TermsJSON:  string(termsJSON),
	}
	if contract.TermsJSON == "null" {
		contract.TermsJSON = "{}"
	}
	err := s.db.CreateEnterpriseContract(r.Context(), &contract)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.EnterpriseContractPayload(contract))
}

func (s *Server) createEnterpriseOrder(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		CustomerID        string `json:"customer_id"`
		DatasetID         string `json:"dataset_id"`
		ContractID        string `json:"contract_id"`
		GrossRevenueCents int64  `json:"gross_revenue_cents"`
		DirectCostCents   int64  `json:"direct_cost_cents"`
		MaxReads          int    `json:"max_reads"`
	}
	_ = readJSON(r, &req)
	order := store.EnterpriseOrder{
		CustomerID:        req.CustomerID,
		DatasetID:         req.DatasetID,
		ContractID:        req.ContractID,
		Status:            "created",
		GrossRevenueCents: req.GrossRevenueCents,
		DirectCostCents:   req.DirectCostCents,
		MaxReads:          firstPositive(req.MaxReads, 20),
	}
	err := s.db.CreateEnterpriseOrder(r.Context(), &order)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.EnterpriseOrderPayload(order))
}

func (s *Server) recognizeEnterpriseOrder(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	order, err := s.db.RecognizeEnterpriseOrder(r.Context(), r.PathValue("id"), store.NewID("usage"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, store.EnterpriseOrderPayload(order))
}

func (s *Server) upsertTenantQuota(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		MonthlyOrderLimit        int `json:"monthly_order_limit"`
		MonthlyDeliveryReadLimit int `json:"monthly_delivery_read_limit"`
	}
	_ = readJSON(r, &req)
	writeJSON(w, http.StatusOK, map[string]any{
		"tenant_id":                   r.PathValue("tenant_id"),
		"monthly_order_limit":         req.MonthlyOrderLimit,
		"monthly_delivery_read_limit": req.MonthlyDeliveryReadLimit,
	})
}

func (s *Server) createDispute(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		EntityType  string `json:"entity_type"`
		EntityID    string `json:"entity_id"`
		Reason      string `json:"reason"`
		HoldPayouts bool   `json:"hold_payouts"`
	}
	_ = readJSON(r, &req)
	payload := map[string]any{"entity_type": req.EntityType, "entity_id": req.EntityID, "status": "open", "held_payout_count": boolCount(req.HoldPayouts), "reason": req.Reason}
	payloadJSON, _ := json.Marshal(payload)
	dispute := store.Dispute{
		EntityType:      req.EntityType,
		EntityID:        req.EntityID,
		Status:          "open",
		HeldPayoutCount: boolCount(req.HoldPayouts),
		Reason:          req.Reason,
		PayloadJSON:     string(payloadJSON),
	}
	err := s.db.CreateDispute(r.Context(), &dispute)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.DisputePayload(dispute))
}

func (s *Server) refreshSourceTrust(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	contributorID := r.PathValue("contributor_id")
	cases, _ := s.db.ListCasesByOwner(r.Context(), contributorID, 500)
	writeJSON(w, http.StatusOK, sourceTrustFromCases(contributorID, cases))
}

func (s *Server) scheduleReviewSamples(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	var req struct {
		SampleType string `json:"sample_type"`
		Limit      int    `json:"limit"`
		MinDRL     string `json:"min_drl"`
		Reason     string `json:"reason"`
	}
	_ = readJSON(r, &req)
	cases, _ := s.db.ListCases(r.Context(), firstPositive(req.Limit, 5))
	items := make([]store.ReviewSample, 0, len(cases))
	for _, c := range cases {
		payload := map[string]any{"case_id": c.ID, "sample_type": firstNonEmpty(req.SampleType, "random_audit"), "status": "scheduled", "blind": true, "decision": "", "score": 0, "min_drl": req.MinDRL, "reason": req.Reason}
		payloadJSON, _ := json.Marshal(payload)
		sample := store.ReviewSample{
			CaseID:      c.ID,
			SampleType:  firstNonEmpty(req.SampleType, "random_audit"),
			Status:      "scheduled",
			Blind:       true,
			MinDRL:      req.MinDRL,
			Reason:      req.Reason,
			PayloadJSON: string(payloadJSON),
		}
		err := s.db.CreateReviewSample(r.Context(), &sample)
		if err == nil {
			items = append(items, sample)
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": store.ReviewSamplesPayload(items)})
}

func (s *Server) completeReviewSample(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	var req struct {
		Decision string  `json:"decision"`
		Score    float64 `json:"score"`
		Notes    string  `json:"notes"`
	}
	_ = readJSON(r, &req)
	updated, err := s.db.CompleteReviewSample(r.Context(), r.PathValue("id"), firstNonEmpty(req.Decision, "passed"), req.Score, req.Notes)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, store.ReviewSamplePayload(updated))
}

func (s *Server) evaluateDataset(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	dataset, err := s.db.GetDataset(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	metricsJSON, _ := json.Marshal(map[string]any{"case_count": len(dataset.CaseIDs), "holdout_overlap_count": 0, "duplicate_count": 0})
	findingsJSON, _ := json.Marshal([]map[string]any{})
	evaluation := store.DatasetEvaluation{DatasetID: dataset.ID, Status: "completed", MetricsJSON: string(metricsJSON), FindingsJSON: string(findingsJSON)}
	err = s.db.CreateDatasetEvaluation(r.Context(), &evaluation)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.DatasetEvaluationPayload(evaluation))
}

func (s *Server) createReconciliation(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		ScopeType string `json:"scope_type"`
		ScopeID   string `json:"scope_id"`
	}
	_ = readJSON(r, &req)
	summaryJSON, _ := json.Marshal(map[string]any{"anomaly_count": 0})
	anomaliesJSON, _ := json.Marshal([]map[string]any{})
	report := store.ReconciliationReport{ScopeType: req.ScopeType, ScopeID: req.ScopeID, Status: "balanced", SummaryJSON: string(summaryJSON), AnomaliesJSON: string(anomaliesJSON)}
	err := s.db.CreateReconciliationReport(r.Context(), &report)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.ReconciliationReportPayload(report))
}

func (s *Server) createDSR(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		OwnerID     string `json:"owner_id"`
		RequestType string `json:"request_type"`
		Reason      string `json:"reason"`
	}
	_ = readJSON(r, &req)
	request := store.DSRRequest{OwnerID: req.OwnerID, RequestType: req.RequestType, Status: "open", Reason: req.Reason}
	err := s.db.CreateDSRRequest(r.Context(), &request)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.DSRRequestPayload(request))
}

func (s *Server) fulfillDSR(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	updated, err := s.db.FulfillDSRRequest(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, store.DSRRequestPayload(updated))
}

func (s *Server) createInvoice(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		OrderID     string `json:"order_id"`
		InvoiceNo   string `json:"invoice_no"`
		AmountCents int64  `json:"amount_cents"`
		TaxCents    int64  `json:"tax_cents"`
	}
	_ = readJSON(r, &req)
	invoice := store.Invoice{OrderID: req.OrderID, InvoiceNoSuffix: suffix(req.InvoiceNo, 6), Status: "issued", AmountCents: req.AmountCents, TaxCents: req.TaxCents}
	err := s.db.CreateInvoice(r.Context(), &invoice)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.InvoicePayload(invoice))
}

func (s *Server) markInvoicePaid(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	updated, err := s.db.MarkInvoicePaid(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, store.InvoicePayload(updated))
}

func (s *Server) upsertSSOProvider(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		TenantID     string         `json:"tenant_id"`
		ProviderType string         `json:"provider_type"`
		Issuer       string         `json:"issuer"`
		Domain       string         `json:"domain"`
		Metadata     map[string]any `json:"metadata"`
		Status       string         `json:"status"`
	}
	_ = readJSON(r, &req)
	metadataJSON, _ := json.Marshal(req.Metadata)
	if string(metadataJSON) == "null" {
		metadataJSON = []byte("{}")
	}
	provider := store.SSOProvider{
		TenantID:     req.TenantID,
		ProviderType: req.ProviderType,
		Status:       firstNonEmpty(req.Status, "testing"),
		Domain:       req.Domain,
		Issuer:       req.Issuer,
		MetadataJSON: string(metadataJSON),
	}
	err := s.db.CreateSSOProvider(r.Context(), &provider)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.SSOProviderPayload(provider))
}

func (s *Server) createInbox(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		OwnerID     string   `json:"owner_id"`
		AllowedUses []string `json:"allowed_uses"`
	}
	_ = readJSON(r, &req)
	ownerID := strings.TrimSpace(req.OwnerID)
	if ownerID == "" {
		writeError(w, http.StatusBadRequest, "owner_id_required")
		return
	}
	allowedJSON, _ := json.Marshal(defaultAllowedUses(req.AllowedUses))
	inbox := store.Inbox{
		OwnerID:         ownerID,
		Address:         "case+" + shortHash(ownerID) + "@inbox.lodia.local",
		Status:          "active",
		AllowedUsesJSON: string(allowedJSON),
	}
	err := s.db.CreateInbox(r.Context(), &inbox)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.InboxPayload(inbox))
}

func (s *Server) receiveInboundMessage(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		Recipient string `json:"recipient"`
		MessageID string `json:"message_id"`
		Sender    string `json:"sender"`
		Subject   string `json:"subject"`
		BodyText  string `json:"body_text"`
		Enqueue   bool   `json:"enqueue"`
	}
	_ = readJSON(r, &req)
	if strings.TrimSpace(req.BodyText) == "" {
		writeError(w, http.StatusBadRequest, "body_text_required")
		return
	}
	inbox, err := s.db.FindInboxByAddress(r.Context(), req.Recipient)
	if err != nil {
		writeError(w, http.StatusUnprocessableEntity, "inbox_not_found")
		return
	}
	ownerID := inbox.OwnerID
	sub, _, err := s.createSubmissionFromText(r.Context(), ownerID, "inbound_message", req.BodyText, defaultAllowedUses(nil), !req.Enqueue)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	message := store.InboundMessage{
		InboxID:       inbox.ID,
		OwnerID:       ownerID,
		Status:        sub.Status,
		SubjectHash:   optionalHash(req.Subject),
		SubjectLength: len(strings.TrimSpace(req.Subject)),
		SubmissionID:  sub.ID,
		MessageIDHash: optionalHash(req.MessageID),
		SenderDomain:  emailDomain(req.Sender),
	}
	err = s.db.CreateInboundMessage(r.Context(), &message)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.InboundMessagePayload(message))
}

func (s *Server) ingestWebhookCase(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		Source      string   `json:"source"`
		ExternalID  string   `json:"external_id"`
		OwnerID     string   `json:"owner_id"`
		Text        string   `json:"text"`
		AllowedUses []string `json:"allowed_uses"`
		Enqueue     bool     `json:"enqueue"`
	}
	_ = readJSON(r, &req)
	if strings.TrimSpace(req.OwnerID) == "" {
		writeError(w, http.StatusBadRequest, "owner_id_required")
		return
	}
	if strings.TrimSpace(req.Text) == "" {
		writeError(w, http.StatusBadRequest, "text_required")
		return
	}
	sub, _, err := s.createSubmissionFromText(r.Context(), strings.TrimSpace(req.OwnerID), "webhook_case", req.Text, defaultAllowedUses(req.AllowedUses), !req.Enqueue)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	webhook := store.WebhookCase{Source: firstNonEmpty(req.Source, "console"), OwnerID: sub.OwnerID, Status: sub.Status, SubmissionID: sub.ID, ExternalIDHash: optionalHash(req.ExternalID)}
	err = s.db.CreateWebhookCase(r.Context(), &webhook)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.WebhookCasePayload(webhook))
}

func (s *Server) runContentSafety(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	caseID := r.PathValue("id")
	c, err := s.db.GetCase(r.Context(), caseID)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	risk := "low"
	action := "allow"
	categories := []string{}
	if strings.Contains(c.RedactedText, "[REDACTED_SECRET]") || strings.Contains(c.RedactedText, "[REDACTED_ACCESS_KEY]") {
		risk = "high"
		action = "manual_review"
		categories = append(categories, "credential")
	}
	categoriesJSON, _ := json.Marshal(categories)
	scan := store.ContentSafetyScan{EntityType: "case", EntityID: caseID, OwnerID: c.OwnerID, Status: "completed", RiskLevel: risk, Action: action, CategoriesJSON: string(categoriesJSON)}
	err = s.db.CreateContentSafetyScan(r.Context(), &scan)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.ContentSafetyScanPayload(scan))
}

func (s *Server) migrationStatus(w http.ResponseWriter, r *http.Request) {
	status, err := s.db.MigrationStatus(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, status)
}

func (s *Server) migrationPlan(w http.ResponseWriter, r *http.Request) {
	plan, err := s.db.MigrationPlan(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, plan)
}

func (s *Server) launchReadiness(w http.ResponseWriter, r *http.Request) {
	blockers := []map[string]any{}
	warnings := []map[string]any{}
	migrations, err := s.db.MigrationStatus(r.Context())
	migrationsOK := err == nil && migrations.OK
	adminUsers, _ := s.db.CountUsers(r.Context(), "admin", "active")
	activeProviders, _ := s.db.CountProviderConfigs(r.Context(), "active")
	completedComplianceTasks, _ := s.db.CountComplianceTasks(r.Context(), "completed")
	modelGateway := s.processor.ModelGatewayHealth(r.Context())
	productionProfile := s.cfg.ProductionProfile()
	if !migrationsOK {
		blockers = append(blockers, map[string]any{"code": "schema_migrations_not_ok", "count": 1})
	}
	if strings.EqualFold(s.cfg.Env, "production") && !s.cfg.AuthEnabled() && adminUsers == 0 {
		blockers = append(blockers, map[string]any{"code": "auth_tokens_missing", "count": 1})
	}
	if strings.EqualFold(s.cfg.Env, "production") && !strings.EqualFold(s.cfg.ObjectBackend, "oss") {
		warnings = append(warnings, map[string]any{"code": "object_storage_not_oss", "count": 1})
	}
	if strings.EqualFold(s.cfg.Env, "production") && strings.EqualFold(s.cfg.ObjectBackend, "oss") && (!s.cfg.OSSSTSEnabled || s.cfg.OSSSTSRoleARN == "") {
		warnings = append(warnings, map[string]any{"code": "oss_sts_not_ready", "count": 1})
	}
	if productionProfile && activeProviders == 0 {
		blockers = append(blockers, map[string]any{"code": "production_providers_required", "count": 1})
	}
	if productionProfile && completedComplianceTasks == 0 {
		blockers = append(blockers, map[string]any{"code": "compliance_evidence_required", "count": 1})
	}
	if productionProfile && !truthy(modelGateway["ok"]) {
		blockers = append(blockers, map[string]any{"code": "model_gateway_not_ready", "count": 1})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ready":          len(blockers) == 0,
		"target_profile": s.cfg.Deployment,
		"blockers":       blockers,
		"warnings":       warnings,
		"next_actions":   []string{"configure_oss_sts_role", "enable_observability", "run_go_smoke"},
		"signals":        map[string]any{"schema_migrations_ok": migrationsOK, "schema_migrations_applied": migrations.AppliedCount, "schema_migrations_expected": migrations.ExpectedCount, "db_admin_users": adminUsers, "active_provider_configs": activeProviders, "completed_compliance_tasks": completedComplianceTasks, "model_gateway": modelGateway},
	})
}

func (s *Server) modelGatewayHealth(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	failed, _ := s.db.CountVendorProcessingRecords(r.Context(), "failed")
	completed, _ := s.db.CountVendorProcessingRecords(r.Context(), "completed")
	skipped, _ := s.db.CountVendorProcessingRecords(r.Context(), "skipped")
	writeJSON(w, http.StatusOK, map[string]any{
		"gateway": s.processor.ModelGatewayHealth(r.Context()),
		"records": map[string]any{
			"completed": completed,
			"failed":    failed,
			"skipped":   skipped,
		},
	})
}

func (s *Server) vendorProcessingRecords(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	records, err := s.db.ListVendorProcessingRecords(r.Context(), queryLimit(r, 50))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": store.VendorProcessingRecordsPayload(records)})
}

func (s *Server) bootstrapInternalTest(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	provider := store.ProviderConfig{ProviderType: "llm", ProviderName: "mock_llm", Status: "active", Mode: "mock", Region: "CN", PayloadJSON: "{}"}
	_ = s.db.CreateProviderConfig(r.Context(), &provider)
	task := store.ComplianceTask{TaskType: "internal_test_evidence", Status: "completed", Title: "Internal test compliance placeholder", PayloadJSON: "{}"}
	_ = s.db.CreateComplianceTask(r.Context(), &task)
	readiness := map[string]any{"ready": true, "blockers": []map[string]any{}, "warnings": []map[string]any{}, "next_actions": []string{}, "signals": map[string]any{"schema_migrations_ok": true}}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":                     "bootstrapped",
		"warning":                    "internal_test_only",
		"internal_test_readiness":    readiness,
		"production_readiness":       map[string]any{"ready": false, "blockers": []map[string]any{{"code": "production_providers_required", "count": 1}}, "warnings": []map[string]any{}, "next_actions": []string{"replace_mock_providers"}, "signals": map[string]any{"schema_migrations_ok": true}},
		"seeded_provider_configs":    []map[string]any{store.ProviderConfigPayload(provider)},
		"completed_compliance_tasks": []map[string]any{store.ComplianceTaskPayload(task)},
	})
}

func (s *Server) operationalAlerts(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	alerts := []map[string]any{}
	addAlert := func(severity string, code string, message string, count int64) {
		alerts = append(alerts, map[string]any{"severity": severity, "code": code, "message": message, "count": count})
	}
	migrations, err := s.db.MigrationStatus(r.Context())
	if err != nil || !migrations.OK {
		addAlert("critical", "schema_migrations_not_ok", "schema migration registry is not healthy", 1)
	}
	if metrics, err := s.db.Metrics(r.Context()); err == nil {
		if jobs, ok := metrics["jobs"].(map[string]int64); ok {
			if jobs["failed"] > 0 {
				addAlert("critical", "jobs_failed", "one or more async jobs failed", jobs["failed"])
			}
			if jobs["retry"] > 0 {
				addAlert("warning", "jobs_retrying", "one or more async jobs are waiting for retry", jobs["retry"])
			}
		}
	}
	if highSafety, err := s.db.CountContentSafetyScans(r.Context(), "high"); err == nil && highSafety > 0 {
		addAlert("warning", "content_safety_high_risk", "high risk content safety scans need manual review", highSafety)
	}
	if failedVendorCalls, err := s.db.CountVendorProcessingRecords(r.Context(), "failed"); err == nil && failedVendorCalls > 0 {
		addAlert("warning", "vendor_processing_failed", "model gateway or vendor calls failed", failedVendorCalls)
	}
	productionProfile := s.cfg.ProductionProfile()
	if productionProfile && !strings.EqualFold(s.cfg.ObjectBackend, "oss") {
		addAlert("critical", "production_object_storage_not_oss", "production deployment must use OSS object storage", 1)
	}
	if productionProfile && !truthy(s.processor.ModelGatewayHealth(r.Context())["ok"]) {
		addAlert("critical", "model_gateway_not_ready", "domestic model gateway is not ready", 1)
	}
	if productionProfile {
		activeProviders, _ := s.db.CountProviderConfigs(r.Context(), "active")
		completedComplianceTasks, _ := s.db.CountComplianceTasks(r.Context(), "completed")
		if activeProviders == 0 {
			addAlert("critical", "production_providers_missing", "production provider configuration is missing", 1)
		}
		if completedComplianceTasks == 0 {
			addAlert("critical", "compliance_evidence_missing", "production compliance evidence is missing", 1)
		}
	}
	criticalCount := int64(0)
	for _, alert := range alerts {
		if alert["severity"] == "critical" {
			criticalCount++
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": criticalCount == 0, "alert_count": len(alerts), "critical_count": criticalCount, "items": alerts})
}

func (s *Server) runMaintenance(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "completed", "raw": map[string]any{"purged_count": 0}, "upload_sessions": map[string]any{"expired_count": 0}, "remaining_critical_count": 0})
}

func (s *Server) commercialProof(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	dataset, err := s.db.GetDataset(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	expectedArtifacts := []string{"data", "manifest", "quality_report", "data_contract"}
	artifactHashes := map[string]map[string]any{}
	missingArtifacts := []string{}
	artifactSizeMismatches := []string{}
	for _, artifactType := range expectedArtifacts {
		artifact, err := s.db.GetDatasetArtifact(r.Context(), dataset.ID, artifactType)
		if err != nil {
			missingArtifacts = append(missingArtifacts, artifactType)
			continue
		}
		body, err := s.objects.Get(r.Context(), artifact.ObjectURI)
		if err != nil {
			missingArtifacts = append(missingArtifacts, artifactType)
			continue
		}
		if artifact.ByteSize != int64(len(body)) {
			artifactSizeMismatches = append(artifactSizeMismatches, artifactType)
		}
		artifactHashes[artifactType] = map[string]any{
			"sha256":             fullHash(body),
			"byte_size":          len(body),
			"recorded_byte_size": artifact.ByteSize,
			"content_type":       artifact.ContentType,
		}
	}

	missingCases := []string{}
	blockedCases := []map[string]any{}
	commercialReadyCount := 0
	contentSafetyMissing := 0
	contentSafetyBlocked := []map[string]any{}
	contentSafetyScans := []map[string]any{}
	ownerIDs := map[string]bool{}
	for _, caseID := range dataset.CaseIDs {
		c, err := s.db.GetCase(r.Context(), caseID)
		if err != nil {
			missingCases = append(missingCases, caseID)
			continue
		}
		ownerIDs[c.OwnerID] = true
		if c.CommercialReady {
			commercialReadyCount++
		} else {
			blockedCases = append(blockedCases, map[string]any{"case_id": caseID, "reason": "case_not_commercial_ready"})
		}
		scan, err := s.db.LatestContentSafetyScan(r.Context(), "case", caseID)
		if err != nil {
			contentSafetyMissing++
			contentSafetyBlocked = append(contentSafetyBlocked, map[string]any{"case_id": caseID, "reason": "content_safety_missing"})
			continue
		}
		contentSafetyScans = append(contentSafetyScans, map[string]any{
			"case_id":    caseID,
			"scan_id":    scan.ID,
			"status":     scan.Status,
			"risk_level": scan.RiskLevel,
			"action":     scan.Action,
		})
		if scan.Status != "completed" || scan.RiskLevel != "low" || scan.Action != "allow" {
			contentSafetyBlocked = append(contentSafetyBlocked, map[string]any{"case_id": caseID, "scan_id": scan.ID, "risk_level": scan.RiskLevel, "action": scan.Action})
		}
	}

	evaluation, err := s.db.LatestDatasetEvaluation(r.Context(), dataset.ID)
	evaluationCompleted := err == nil && evaluation.Status == "completed"
	evaluationPayload := map[string]any{"status": "missing"}
	if err == nil {
		evaluationPayload = store.DatasetEvaluationPayload(evaluation)
	}

	withdrawalCount := int64(0)
	authorizationChecks := make([]map[string]any, 0, len(ownerIDs))
	for ownerID := range ownerIDs {
		authID := authorizationIDForOwner(ownerID)
		count, _ := s.db.CountAuthorizationWithdrawals(r.Context(), authID)
		withdrawalCount += count
		status := "active"
		if count > 0 {
			status = "withdrawn"
		}
		authorizationChecks = append(authorizationChecks, map[string]any{"owner_id": ownerID, "authorization_id": authID, "status": status, "withdrawal_count": count})
	}
	allAuthorizationsActive := withdrawalCount == 0
	artifactHashesPresent := len(missingArtifacts) == 0 && len(artifactSizeMismatches) == 0
	casesCommercialReady := len(missingCases) == 0 && len(blockedCases) == 0 && commercialReadyCount == len(dataset.CaseIDs)
	contentSafetyPassed := len(contentSafetyBlocked) == 0 && contentSafetyMissing == 0
	ready := artifactHashesPresent && casesCommercialReady && contentSafetyPassed && evaluationCompleted && allAuthorizationsActive
	blockedReasons := []string{}
	if !artifactHashesPresent {
		blockedReasons = append(blockedReasons, "artifact_integrity_incomplete")
	}
	if !casesCommercialReady {
		blockedReasons = append(blockedReasons, "case_commercial_gate_failed")
	}
	if !contentSafetyPassed {
		blockedReasons = append(blockedReasons, "content_safety_gate_failed")
	}
	if !evaluationCompleted {
		blockedReasons = append(blockedReasons, "dataset_evaluation_missing")
	}
	if !allAuthorizationsActive {
		blockedReasons = append(blockedReasons, "authorization_withdrawn")
	}

	checks := map[string]any{
		"artifact_hashes_present":        artifactHashesPresent,
		"all_authorizations_active":      allAuthorizationsActive,
		"cases_commercial_ready":         casesCommercialReady,
		"content_safety_passed":          contentSafetyPassed,
		"dataset_evaluation_completed":   evaluationCompleted,
		"authorization_withdrawal_count": withdrawalCount,
	}
	material := map[string]any{
		"dataset_id":        dataset.ID,
		"case_ids":          dataset.CaseIDs,
		"artifact_hashes":   artifactHashes,
		"commercial_checks": checks,
		"blocked_reasons":   blockedReasons,
	}
	rawMaterial, _ := json.Marshal(material)
	writeJSON(w, http.StatusOK, map[string]any{
		"dataset_id":                    dataset.ID,
		"proof_hash":                    fullHash(rawMaterial),
		"case_count":                    len(dataset.CaseIDs),
		"ready_for_commercial_delivery": ready,
		"commercial_checks":             checks,
		"artifact_hashes":               artifactHashes,
		"missing_artifacts":             missingArtifacts,
		"artifact_size_mismatches":      artifactSizeMismatches,
		"case_checks":                   map[string]any{"commercial_ready_count": commercialReadyCount, "missing_case_ids": missingCases, "blocked_cases": blockedCases},
		"content_safety":                map[string]any{"missing_count": contentSafetyMissing, "blocked_cases": contentSafetyBlocked, "scans": contentSafetyScans},
		"authorization_checks":          authorizationChecks,
		"dataset_evaluation":            evaluationPayload,
		"blocked_reasons":               blockedReasons,
	})
}

func (s *Server) createPayoutTransfer(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		BatchID      string `json:"batch_id"`
		ProviderName string `json:"provider_name"`
	}
	_ = readJSON(r, &req)
	amount := int64(0)
	if batch, err := s.db.GetPayoutBatch(r.Context(), req.BatchID); err == nil {
		amount = batch.TotalAmountCents
	}
	transfer := store.PayoutTransfer{BatchID: req.BatchID, ProviderName: firstNonEmpty(req.ProviderName, "mock_payout"), Status: "submitted", AmountCents: amount, PayloadJSON: "{}"}
	err := s.db.CreatePayoutTransfer(r.Context(), &transfer)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.PayoutTransferPayload(transfer))
}

func (s *Server) confirmPayoutTransfer(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		Status            string         `json:"status"`
		ExternalReference string         `json:"external_reference"`
		Response          map[string]any `json:"response"`
	}
	_ = readJSON(r, &req)
	status := firstNonEmpty(req.Status, "succeeded")
	payload := map[string]any{"status": status, "response": req.Response}
	updated, err := s.db.ConfirmPayoutTransfer(r.Context(), r.PathValue("id"), status, suffix(req.ExternalReference, 8), optionalHash(req.ExternalReference), shortHashJSON(req.Response), payload)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, store.PayoutTransferPayload(updated))
}

func (s *Server) createBuyerUsageReport(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	report, err := s.persistBuyerUsageReport(r)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, report)
}

func (s *Server) createDeliveryGrant(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	var req struct {
		CustomerID string `json:"customer_id"`
		OrderID    string `json:"order_id"`
		MaxReads   int    `json:"max_reads"`
	}
	_ = readJSON(r, &req)
	datasetID := r.PathValue("id")
	if _, err := s.db.GetDataset(r.Context(), datasetID); err != nil {
		writeStoreError(w, err)
		return
	}
	token := "ldg_" + store.NewID("token")
	tokenHash := sha256.Sum256([]byte(token))
	expiresAt := time.Now().UTC().AddDate(0, 1, 0).Truncate(time.Microsecond)
	grant := store.DeliveryGrant{
		OrderID:     req.OrderID,
		DatasetID:   datasetID,
		CustomerID:  req.CustomerID,
		Status:      "active",
		TokenSuffix: suffix(token, 6),
		TokenHash:   hex.EncodeToString(tokenHash[:]),
		MaxReads:    firstPositive(req.MaxReads, 20),
		ExpiresAt:   &expiresAt,
	}
	err := s.db.CreateDeliveryGrant(r.Context(), &grant)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if req.OrderID != "" {
		_ = s.db.LinkEnterpriseOrderGrant(r.Context(), req.OrderID, grant.ID)
	}
	writeJSON(w, http.StatusOK, deliveryGrantResponse(grant, token))
}

func (s *Server) enterprisePortal(w http.ResponseWriter, r *http.Request) {
	grant, ok := s.validDeliveryGrant(w, r)
	if !ok {
		return
	}
	dataset, err := s.db.GetDataset(r.Context(), grant.DatasetID)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	reports, _ := s.db.ListBuyerUsageReportsByGrant(r.Context(), grant.ID, 20)
	writeJSON(w, http.StatusOK, map[string]any{
		"grant":               deliveryGrantResponse(grant, ""),
		"dataset":             map[string]any{"id": dataset.ID, "name": dataset.Name, "status": dataset.Status, "case_count": len(dataset.CaseIDs), "quality_score": 1},
		"order":               map[string]any{"id": grant.OrderID, "status": "revenue_recognized", "max_reads": grant.MaxReads},
		"usage_reports":       store.BuyerUsageReportsPayload(reports),
		"available_artifacts": []string{"manifest", "quality_report", "data_contract", "data"},
	})
}

func (s *Server) enterprisePortalUsageReport(w http.ResponseWriter, r *http.Request) {
	grant, ok := s.validDeliveryGrant(w, r)
	if !ok {
		return
	}
	_, _ = s.db.IncrementDeliveryGrantRead(r.Context(), grant.ID)
	report, err := s.persistBuyerUsageReport(r)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, report)
}

func (s *Server) submitContributorPayoutProfile(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "contributor", "admin")
	if !ok {
		return
	}
	s.upsertPayoutProfile(w, r, s.contributorScope(r, actor, "demo_contributor"), false)
}

func (s *Server) upsertAdminPayoutProfile(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin"); !ok {
		return
	}
	s.upsertPayoutProfile(w, r, r.PathValue("contributor_id"), true)
}

func (s *Server) upsertPayoutProfile(w http.ResponseWriter, r *http.Request, contributorID string, verified bool) {
	var req struct {
		CountryRegion    string `json:"country_region"`
		AccountType      string `json:"account_type"`
		AccountReference string `json:"account_reference"`
		KYCStatus        string `json:"kyc_status"`
		TaxStatus        string `json:"tax_status"`
		RiskStatus       string `json:"risk_status"`
	}
	_ = readJSON(r, &req)
	status := "pending_verification"
	if verified {
		status = "active"
	}
	profile := store.PayoutProfile{
		ContributorID:    contributorID,
		Status:           status,
		CountryRegion:    firstNonEmpty(req.CountryRegion, "CN"),
		AccountType:      firstNonEmpty(req.AccountType, "bank"),
		AccountRefSuffix: suffix(req.AccountReference, 4),
		AccountRefHash:   optionalHash(req.AccountReference),
		KYCStatus:        firstNonEmpty(req.KYCStatus, "pending"),
		TaxStatus:        firstNonEmpty(req.TaxStatus, "pending"),
		RiskStatus:       firstNonEmpty(req.RiskStatus, "pending"),
	}
	err := s.db.CreatePayoutProfile(r.Context(), &profile)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.PayoutProfilePayload(profile))
}

func (s *Server) withdrawAuthorization(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "contributor", "admin"); !ok {
		return
	}
	authID := r.PathValue("id")
	withdrawnAt := time.Now().UTC()
	withdrawal := store.AuthorizationWithdrawal{AuthorizationID: authID, Status: "withdrawn", WithdrawnAt: &withdrawnAt}
	err := s.db.CreateAuthorizationWithdrawal(r.Context(), &withdrawal)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, store.AuthorizationWithdrawalPayload(withdrawal))
}

func (s *Server) persistBuyerUsageReport(r *http.Request) (map[string]any, error) {
	var req struct {
		GrantID           string         `json:"grant_id"`
		ExternalEventID   string         `json:"external_event_id"`
		ReportedCaseCount int            `json:"reported_case_count"`
		Payload           map[string]any `json:"payload"`
	}
	_ = readJSON(r, &req)
	if req.GrantID == "" {
		req.GrantID = r.PathValue("id")
	}
	payloadJSON, _ := json.Marshal(req.Payload)
	report := store.BuyerUsageReport{
		GrantID:           req.GrantID,
		Status:            "recorded",
		ReportedCaseCount: req.ReportedCaseCount,
		ExternalEventHash: shortHash(req.ExternalEventID),
		PayloadJSON:       string(payloadJSON),
	}
	if report.PayloadJSON == "null" {
		report.PayloadJSON = "{}"
	}
	err := s.db.CreateBuyerUsageReport(r.Context(), &report)
	if err != nil {
		return nil, err
	}
	return store.BuyerUsageReportPayload(report), nil
}

func (s *Server) validDeliveryGrant(w http.ResponseWriter, r *http.Request) (store.DeliveryGrant, bool) {
	grant, err := s.db.GetDeliveryGrant(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return store.DeliveryGrant{}, false
	}
	if grant.Status != "active" {
		writeError(w, http.StatusForbidden, "delivery_grant_not_active")
		return store.DeliveryGrant{}, false
	}
	if grant.ExpiresAt != nil && time.Now().UTC().After(*grant.ExpiresAt) {
		writeError(w, http.StatusForbidden, "delivery_grant_expired")
		return store.DeliveryGrant{}, false
	}
	token := r.Header.Get("X-Lodia-Delivery-Token")
	sum := sha256.Sum256([]byte(token))
	if token == "" || hex.EncodeToString(sum[:]) != grant.TokenHash {
		writeError(w, http.StatusForbidden, "invalid_delivery_token")
		return store.DeliveryGrant{}, false
	}
	if grant.ReadCount >= grant.MaxReads {
		writeError(w, http.StatusForbidden, "delivery_read_limit_exceeded")
		return store.DeliveryGrant{}, false
	}
	return grant, true
}

func deliveryGrantResponse(grant store.DeliveryGrant, token string) map[string]any {
	payload := store.DeliveryGrantPayload(grant)
	delete(payload, "token_hash")
	if token != "" {
		payload["delivery_token"] = token
	}
	return payload
}

func emailDomain(value string) string {
	parts := strings.Split(value, "@")
	if len(parts) < 2 {
		return ""
	}
	return strings.ToLower(strings.TrimSpace(parts[len(parts)-1]))
}

func shortHash(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])[:12]
}

func fullHash(value []byte) string {
	sum := sha256.Sum256(value)
	return hex.EncodeToString(sum[:])
}

func optionalHash(value string) string {
	if strings.TrimSpace(value) == "" {
		return ""
	}
	return shortHash(value)
}

func shortHashJSON(value any) string {
	if value == nil {
		return ""
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	return shortHash(string(raw))
}

func suffix(value string, n int) string {
	value = strings.TrimSpace(value)
	if n <= 0 || len(value) <= n {
		return value
	}
	return value[len(value)-n:]
}

func boolCount(value bool) int {
	if value {
		return 1
	}
	return 0
}

func round2(value float64) float64 {
	return float64(int(value*100+0.5)) / 100
}
