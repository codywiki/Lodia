package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"
)

type Dispute struct {
	ID              string
	EntityType      string
	EntityID        string
	Status          string
	HeldPayoutCount int
	Reason          string
	PayloadJSON     string
	CreatedAt       time.Time
	UpdatedAt       time.Time
}

type ReviewSample struct {
	ID          string
	CaseID      string
	SampleType  string
	Status      string
	Blind       bool
	Decision    string
	Score       float64
	MinDRL      string
	Reason      string
	Notes       string
	PayloadJSON string
	CreatedAt   time.Time
	UpdatedAt   time.Time
}

type DatasetEvaluation struct {
	ID           string
	DatasetID    string
	Status       string
	MetricsJSON  string
	FindingsJSON string
	CreatedAt    time.Time
	UpdatedAt    time.Time
}

type ReconciliationReport struct {
	ID            string
	ScopeType     string
	ScopeID       string
	Status        string
	SummaryJSON   string
	AnomaliesJSON string
	CreatedAt     time.Time
	UpdatedAt     time.Time
}

type Invoice struct {
	ID              string
	OrderID         string
	InvoiceNoSuffix string
	Status          string
	AmountCents     int64
	TaxCents        int64
	CreatedAt       time.Time
	UpdatedAt       time.Time
}

type SSOProvider struct {
	ID           string
	TenantID     string
	ProviderType string
	Status       string
	Domain       string
	Issuer       string
	MetadataJSON string
	CreatedAt    time.Time
	UpdatedAt    time.Time
}

type Inbox struct {
	ID              string
	OwnerID         string
	Address         string
	Status          string
	AllowedUsesJSON string
	CreatedAt       time.Time
	UpdatedAt       time.Time
}

type InboundMessage struct {
	ID            string
	InboxID       string
	OwnerID       string
	Status        string
	SubjectHash   string
	SubjectLength int
	SubmissionID  string
	MessageIDHash string
	SenderDomain  string
	CreatedAt     time.Time
	UpdatedAt     time.Time
}

type WebhookCase struct {
	ID             string
	Source         string
	OwnerID        string
	Status         string
	SubmissionID   string
	ExternalIDHash string
	CreatedAt      time.Time
	UpdatedAt      time.Time
}

type ContentSafetyScan struct {
	ID             string
	EntityType     string
	EntityID       string
	OwnerID        string
	Status         string
	RiskLevel      string
	Action         string
	CategoriesJSON string
	CreatedAt      time.Time
	UpdatedAt      time.Time
}

type VendorProcessingRecord struct {
	ID                 string
	ProviderType       string
	ProviderName       string
	Operation          string
	EntityType         string
	EntityID           string
	Status             string
	Region             string
	DataClassification string
	InputHash          string
	OutputHash         string
	PromptVersion      string
	ModelName          string
	LatencyMS          int64
	InputTokens        int
	OutputTokens       int
	CostMicros         int64
	ErrorCode          string
	MetadataJSON       string
	CreatedAt          time.Time
}

type PayoutProfile struct {
	ID               string
	ContributorID    string
	Status           string
	CountryRegion    string
	AccountType      string
	AccountRefSuffix string
	AccountRefHash   string
	KYCStatus        string
	TaxStatus        string
	RiskStatus       string
	CreatedAt        time.Time
	UpdatedAt        time.Time
}

type AuthorizationWithdrawal struct {
	ID              string
	AuthorizationID string
	Status          string
	WithdrawnAt     *time.Time
	CreatedAt       time.Time
	UpdatedAt       time.Time
}

func (db *DB) CreateDispute(ctx context.Context, dispute *Dispute) error {
	now := nowUTC()
	if dispute.ID == "" {
		dispute.ID = NewID("dispute")
	}
	if dispute.Status == "" {
		dispute.Status = "open"
	}
	if dispute.PayloadJSON == "" {
		dispute.PayloadJSON = "{}"
	}
	dispute.CreatedAt = now
	dispute.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO disputes
			(id, entity_type, entity_id, status, held_payout_count, reason, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, dispute.ID, dispute.EntityType, dispute.EntityID, dispute.Status, dispute.HeldPayoutCount, dispute.Reason, dispute.PayloadJSON, dispute.CreatedAt, dispute.UpdatedAt)
	return err
}

func (db *DB) CreateReviewSample(ctx context.Context, sample *ReviewSample) error {
	now := nowUTC()
	if sample.ID == "" {
		sample.ID = NewID("sample")
	}
	if sample.SampleType == "" {
		sample.SampleType = "random_audit"
	}
	if sample.Status == "" {
		sample.Status = "scheduled"
	}
	if sample.PayloadJSON == "" {
		sample.PayloadJSON = "{}"
	}
	sample.CreatedAt = now
	sample.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO review_samples
			(id, case_id, sample_type, status, blind, decision, score, min_drl, reason, notes, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, sample.ID, sample.CaseID, sample.SampleType, sample.Status, sample.Blind, sample.Decision, sample.Score, sample.MinDRL, sample.Reason, sample.Notes, sample.PayloadJSON, sample.CreatedAt, sample.UpdatedAt)
	return err
}

func (db *DB) GetReviewSample(ctx context.Context, id string) (ReviewSample, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, case_id, sample_type, status, blind, decision, score, min_drl, reason, notes, CAST(payload_json AS CHAR), created_at, updated_at
		FROM review_samples WHERE id = ?
	`, id)
	return scanReviewSample(row)
}

func (db *DB) CompleteReviewSample(ctx context.Context, id string, decision string, score float64, notes string) (ReviewSample, error) {
	if decision == "" {
		decision = "passed"
	}
	_, err := db.sql.ExecContext(ctx, `
		UPDATE review_samples
		SET status = 'completed', decision = ?, score = ?, notes = ?, updated_at = ?
		WHERE id = ?
	`, decision, score, notes, nowUTC(), id)
	if err != nil {
		return ReviewSample{}, err
	}
	return db.GetReviewSample(ctx, id)
}

func (db *DB) CreateDatasetEvaluation(ctx context.Context, evaluation *DatasetEvaluation) error {
	now := nowUTC()
	if evaluation.ID == "" {
		evaluation.ID = NewID("eval")
	}
	if evaluation.Status == "" {
		evaluation.Status = "completed"
	}
	if evaluation.MetricsJSON == "" {
		evaluation.MetricsJSON = "{}"
	}
	if evaluation.FindingsJSON == "" {
		evaluation.FindingsJSON = "[]"
	}
	evaluation.CreatedAt = now
	evaluation.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO dataset_evaluations
			(id, dataset_id, status, metrics_json, findings_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, evaluation.ID, evaluation.DatasetID, evaluation.Status, evaluation.MetricsJSON, evaluation.FindingsJSON, evaluation.CreatedAt, evaluation.UpdatedAt)
	return err
}

func (db *DB) LatestDatasetEvaluation(ctx context.Context, datasetID string) (DatasetEvaluation, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, dataset_id, status, CAST(metrics_json AS CHAR), CAST(findings_json AS CHAR), created_at, updated_at
		FROM dataset_evaluations WHERE dataset_id = ? ORDER BY created_at DESC LIMIT 1
	`, datasetID)
	return scanDatasetEvaluation(row)
}

func (db *DB) CountDatasetEvaluations(ctx context.Context, status string) (int64, error) {
	query := `SELECT COUNT(*) FROM dataset_evaluations`
	args := []any{}
	if status != "" {
		query += ` WHERE status = ?`
		args = append(args, status)
	}
	var count int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&count)
	return count, err
}

func (db *DB) CreateReconciliationReport(ctx context.Context, report *ReconciliationReport) error {
	now := nowUTC()
	if report.ID == "" {
		report.ID = NewID("recon")
	}
	if report.Status == "" {
		report.Status = "balanced"
	}
	if report.SummaryJSON == "" {
		report.SummaryJSON = "{}"
	}
	if report.AnomaliesJSON == "" {
		report.AnomaliesJSON = "[]"
	}
	report.CreatedAt = now
	report.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO reconciliation_reports
			(id, scope_type, scope_id, status, summary_json, anomalies_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, report.ID, report.ScopeType, report.ScopeID, report.Status, report.SummaryJSON, report.AnomaliesJSON, report.CreatedAt, report.UpdatedAt)
	return err
}

func (db *DB) CreateInvoice(ctx context.Context, invoice *Invoice) error {
	now := nowUTC()
	if invoice.ID == "" {
		invoice.ID = NewID("invoice")
	}
	if invoice.Status == "" {
		invoice.Status = "issued"
	}
	invoice.CreatedAt = now
	invoice.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO invoices
			(id, order_id, invoice_no_suffix, status, amount_cents, tax_cents, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, invoice.ID, invoice.OrderID, invoice.InvoiceNoSuffix, invoice.Status, invoice.AmountCents, invoice.TaxCents, invoice.CreatedAt, invoice.UpdatedAt)
	return err
}

func (db *DB) GetInvoice(ctx context.Context, id string) (Invoice, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, order_id, invoice_no_suffix, status, amount_cents, tax_cents, created_at, updated_at
		FROM invoices WHERE id = ?
	`, id)
	return scanInvoice(row)
}

func (db *DB) MarkInvoicePaid(ctx context.Context, id string) (Invoice, error) {
	_, err := db.sql.ExecContext(ctx, `
		UPDATE invoices SET status = 'paid', updated_at = ? WHERE id = ?
	`, nowUTC(), id)
	if err != nil {
		return Invoice{}, err
	}
	return db.GetInvoice(ctx, id)
}

func (db *DB) CreateSSOProvider(ctx context.Context, provider *SSOProvider) error {
	now := nowUTC()
	if provider.ID == "" {
		provider.ID = NewID("sso")
	}
	if provider.Status == "" {
		provider.Status = "testing"
	}
	if provider.MetadataJSON == "" {
		provider.MetadataJSON = "{}"
	}
	provider.CreatedAt = now
	provider.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO sso_providers
			(id, tenant_id, provider_type, status, domain, issuer, metadata_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, provider.ID, provider.TenantID, provider.ProviderType, provider.Status, provider.Domain, provider.Issuer, provider.MetadataJSON, provider.CreatedAt, provider.UpdatedAt)
	return err
}

func (db *DB) CreateInbox(ctx context.Context, inbox *Inbox) error {
	if existing, err := db.FindInboxByAddress(ctx, inbox.Address); err == nil {
		*inbox = existing
		return nil
	} else if !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	now := nowUTC()
	if inbox.ID == "" {
		inbox.ID = NewID("inbox")
	}
	if inbox.Status == "" {
		inbox.Status = "active"
	}
	if inbox.AllowedUsesJSON == "" {
		inbox.AllowedUsesJSON = "[]"
	}
	inbox.CreatedAt = now
	inbox.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO inboxes
			(id, owner_id, address, status, allowed_uses_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, inbox.ID, inbox.OwnerID, inbox.Address, inbox.Status, inbox.AllowedUsesJSON, inbox.CreatedAt, inbox.UpdatedAt)
	return err
}

func (db *DB) FindInboxByAddress(ctx context.Context, address string) (Inbox, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, owner_id, address, status, CAST(allowed_uses_json AS CHAR), created_at, updated_at
		FROM inboxes WHERE address = ?
	`, address)
	return scanInbox(row)
}

func (db *DB) CountInboxes(ctx context.Context, status string) (int64, error) {
	query := `SELECT COUNT(*) FROM inboxes`
	args := []any{}
	if status != "" {
		query += ` WHERE status = ?`
		args = append(args, status)
	}
	var count int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&count)
	return count, err
}

func (db *DB) CountInboxesByOwner(ctx context.Context, ownerID string, status string) (int64, error) {
	query := `SELECT COUNT(*) FROM inboxes WHERE owner_id = ?`
	args := []any{ownerID}
	if status != "" {
		query += ` AND status = ?`
		args = append(args, status)
	}
	var count int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&count)
	return count, err
}

func (db *DB) CreateInboundMessage(ctx context.Context, message *InboundMessage) error {
	now := nowUTC()
	if message.ID == "" {
		message.ID = NewID("inbound")
	}
	if message.Status == "" {
		message.Status = "queued"
	}
	message.CreatedAt = now
	message.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO inbound_messages
			(id, inbox_id, owner_id, status, subject_hash, subject_length, submission_id, message_id_hash, sender_domain, created_at, updated_at)
		VALUES (?, NULLIF(?, ''), ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, message.ID, message.InboxID, message.OwnerID, message.Status, message.SubjectHash, message.SubjectLength, message.SubmissionID, message.MessageIDHash, message.SenderDomain, message.CreatedAt, message.UpdatedAt)
	return err
}

func (db *DB) CreateWebhookCase(ctx context.Context, webhook *WebhookCase) error {
	now := nowUTC()
	if webhook.ID == "" {
		webhook.ID = NewID("webhook")
	}
	if webhook.Source == "" {
		webhook.Source = "console"
	}
	if webhook.Status == "" {
		webhook.Status = "queued"
	}
	webhook.CreatedAt = now
	webhook.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO webhook_cases
			(id, source, owner_id, status, submission_id, external_id_hash, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, webhook.ID, webhook.Source, webhook.OwnerID, webhook.Status, webhook.SubmissionID, webhook.ExternalIDHash, webhook.CreatedAt, webhook.UpdatedAt)
	return err
}

func (db *DB) CreateContentSafetyScan(ctx context.Context, scan *ContentSafetyScan) error {
	now := nowUTC()
	if scan.ID == "" {
		scan.ID = NewID("safety")
	}
	if scan.Status == "" {
		scan.Status = "completed"
	}
	if scan.CategoriesJSON == "" {
		scan.CategoriesJSON = "[]"
	}
	scan.CreatedAt = now
	scan.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO content_safety_scans
			(id, entity_type, entity_id, owner_id, status, risk_level, action, categories_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, scan.ID, scan.EntityType, scan.EntityID, scan.OwnerID, scan.Status, scan.RiskLevel, scan.Action, scan.CategoriesJSON, scan.CreatedAt, scan.UpdatedAt)
	return err
}

func (db *DB) LatestContentSafetyScan(ctx context.Context, entityType string, entityID string) (ContentSafetyScan, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, entity_type, entity_id, owner_id, status, risk_level, action, CAST(categories_json AS CHAR), created_at, updated_at
		FROM content_safety_scans WHERE entity_type = ? AND entity_id = ? ORDER BY created_at DESC LIMIT 1
	`, entityType, entityID)
	return scanContentSafetyScan(row)
}

func (db *DB) CountContentSafetyScans(ctx context.Context, riskLevel string) (int64, error) {
	query := `SELECT COUNT(*) FROM content_safety_scans`
	args := []any{}
	if riskLevel != "" {
		query += ` WHERE risk_level = ?`
		args = append(args, riskLevel)
	}
	var count int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&count)
	return count, err
}

func (db *DB) CreateVendorProcessingRecord(ctx context.Context, record *VendorProcessingRecord) error {
	now := nowUTC()
	if record.ID == "" {
		record.ID = NewID("vendor")
	}
	if record.Status == "" {
		record.Status = "completed"
	}
	if record.MetadataJSON == "" {
		record.MetadataJSON = "{}"
	}
	record.CreatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO vendor_processing_records
			(id, provider_type, provider_name, operation, entity_type, entity_id, status, region, data_classification,
			 input_hash, output_hash, prompt_version, model_name, latency_ms, input_tokens, output_tokens, cost_micros, error_code, metadata_json, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, record.ID, record.ProviderType, record.ProviderName, record.Operation, record.EntityType, record.EntityID, record.Status, record.Region, record.DataClassification,
		record.InputHash, record.OutputHash, record.PromptVersion, record.ModelName, record.LatencyMS, record.InputTokens, record.OutputTokens, record.CostMicros, record.ErrorCode, record.MetadataJSON, record.CreatedAt)
	return err
}

func (db *DB) ListVendorProcessingRecords(ctx context.Context, limit int) ([]VendorProcessingRecord, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, provider_type, provider_name, operation, entity_type, entity_id, status, region, data_classification,
			input_hash, output_hash, prompt_version, model_name, latency_ms, input_tokens, output_tokens, cost_micros, error_code, CAST(metadata_json AS CHAR), created_at
		FROM vendor_processing_records ORDER BY created_at DESC LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	records := []VendorProcessingRecord{}
	for rows.Next() {
		record, err := scanVendorProcessingRecord(rows)
		if err != nil {
			return nil, err
		}
		records = append(records, record)
	}
	return records, rows.Err()
}

func (db *DB) CountVendorProcessingRecords(ctx context.Context, status string) (int64, error) {
	query := `SELECT COUNT(*) FROM vendor_processing_records`
	args := []any{}
	if status != "" {
		query += ` WHERE status = ?`
		args = append(args, status)
	}
	var count int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&count)
	return count, err
}

func (db *DB) CreatePayoutProfile(ctx context.Context, profile *PayoutProfile) error {
	now := nowUTC()
	if profile.ID == "" {
		profile.ID = NewID("profile")
	}
	if profile.Status == "" {
		profile.Status = "pending_verification"
	}
	profile.CreatedAt = now
	profile.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO payout_profiles
			(id, contributor_id, status, country_region, account_type, account_ref_suffix, account_ref_hash, kyc_status, tax_status, risk_status, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, profile.ID, profile.ContributorID, profile.Status, profile.CountryRegion, profile.AccountType, profile.AccountRefSuffix, profile.AccountRefHash, profile.KYCStatus, profile.TaxStatus, profile.RiskStatus, profile.CreatedAt, profile.UpdatedAt)
	return err
}

func (db *DB) LatestPayoutProfileByContributor(ctx context.Context, contributorID string) (PayoutProfile, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, contributor_id, status, country_region, account_type, account_ref_suffix, account_ref_hash, kyc_status, tax_status, risk_status, created_at, updated_at
		FROM payout_profiles WHERE contributor_id = ? ORDER BY created_at DESC LIMIT 1
	`, contributorID)
	return scanPayoutProfile(row)
}

func (db *DB) CreateAuthorizationWithdrawal(ctx context.Context, withdrawal *AuthorizationWithdrawal) error {
	now := nowUTC()
	if withdrawal.ID == "" {
		withdrawal.ID = NewID("authwd")
	}
	if withdrawal.Status == "" {
		withdrawal.Status = "withdrawn"
	}
	if withdrawal.WithdrawnAt == nil {
		value := now
		withdrawal.WithdrawnAt = &value
	}
	withdrawal.CreatedAt = now
	withdrawal.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO authorization_withdrawals
			(id, authorization_id, status, withdrawn_at, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?)
	`, withdrawal.ID, withdrawal.AuthorizationID, withdrawal.Status, withdrawal.WithdrawnAt, withdrawal.CreatedAt, withdrawal.UpdatedAt)
	return err
}

func (db *DB) CountAuthorizationWithdrawals(ctx context.Context, authorizationID string) (int64, error) {
	var count int64
	err := db.sql.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM authorization_withdrawals WHERE authorization_id = ? AND status = 'withdrawn'
	`, authorizationID).Scan(&count)
	return count, err
}

func DisputePayload(dispute Dispute) map[string]any {
	return map[string]any{
		"id":                dispute.ID,
		"entity_type":       dispute.EntityType,
		"entity_id":         dispute.EntityID,
		"status":            dispute.Status,
		"held_payout_count": dispute.HeldPayoutCount,
		"reason":            dispute.Reason,
		"payload":           jsonMap(dispute.PayloadJSON),
		"created_at":        dispute.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":        dispute.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func ReviewSamplePayload(sample ReviewSample) map[string]any {
	return map[string]any{
		"id":          sample.ID,
		"case_id":     sample.CaseID,
		"sample_type": sample.SampleType,
		"status":      sample.Status,
		"blind":       sample.Blind,
		"decision":    sample.Decision,
		"score":       sample.Score,
		"min_drl":     sample.MinDRL,
		"reason":      sample.Reason,
		"notes":       sample.Notes,
		"payload":     jsonMap(sample.PayloadJSON),
		"created_at":  sample.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":  sample.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func ReviewSamplesPayload(samples []ReviewSample) []map[string]any {
	out := make([]map[string]any, 0, len(samples))
	for _, sample := range samples {
		out = append(out, ReviewSamplePayload(sample))
	}
	return out
}

func DatasetEvaluationPayload(evaluation DatasetEvaluation) map[string]any {
	return map[string]any{
		"id":         evaluation.ID,
		"dataset_id": evaluation.DatasetID,
		"status":     evaluation.Status,
		"metrics":    jsonAny(evaluation.MetricsJSON),
		"findings":   jsonAny(evaluation.FindingsJSON),
		"created_at": evaluation.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at": evaluation.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func ReconciliationReportPayload(report ReconciliationReport) map[string]any {
	return map[string]any{
		"id":         report.ID,
		"scope_type": report.ScopeType,
		"scope_id":   report.ScopeID,
		"status":     report.Status,
		"summary":    jsonAny(report.SummaryJSON),
		"anomalies":  jsonAny(report.AnomaliesJSON),
		"created_at": report.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at": report.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func InvoicePayload(invoice Invoice) map[string]any {
	return map[string]any{
		"id":                invoice.ID,
		"order_id":          invoice.OrderID,
		"invoice_no_suffix": invoice.InvoiceNoSuffix,
		"status":            invoice.Status,
		"amount_cents":      invoice.AmountCents,
		"tax_cents":         invoice.TaxCents,
		"created_at":        invoice.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":        invoice.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func SSOProviderPayload(provider SSOProvider) map[string]any {
	return map[string]any{
		"id":            provider.ID,
		"tenant_id":     provider.TenantID,
		"provider_type": provider.ProviderType,
		"status":        provider.Status,
		"domain":        provider.Domain,
		"issuer":        provider.Issuer,
		"metadata":      jsonMap(provider.MetadataJSON),
		"created_at":    provider.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":    provider.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func InboxPayload(inbox Inbox) map[string]any {
	return map[string]any{
		"id":           inbox.ID,
		"owner_id":     inbox.OwnerID,
		"address":      inbox.Address,
		"status":       inbox.Status,
		"allowed_uses": jsonAny(inbox.AllowedUsesJSON),
		"created_at":   inbox.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":   inbox.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func InboundMessagePayload(message InboundMessage) map[string]any {
	return map[string]any{
		"id":              message.ID,
		"inbox_id":        message.InboxID,
		"owner_id":        message.OwnerID,
		"status":          message.Status,
		"subject_hash":    message.SubjectHash,
		"subject_length":  message.SubjectLength,
		"submission_id":   message.SubmissionID,
		"message_id_hash": message.MessageIDHash,
		"sender_domain":   message.SenderDomain,
		"created_at":      message.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":      message.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func WebhookCasePayload(webhook WebhookCase) map[string]any {
	return map[string]any{
		"id":               webhook.ID,
		"source":           webhook.Source,
		"owner_id":         webhook.OwnerID,
		"status":           webhook.Status,
		"submission_id":    webhook.SubmissionID,
		"external_id_hash": webhook.ExternalIDHash,
		"created_at":       webhook.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":       webhook.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func ContentSafetyScanPayload(scan ContentSafetyScan) map[string]any {
	return map[string]any{
		"id":          scan.ID,
		"entity_type": scan.EntityType,
		"entity_id":   scan.EntityID,
		"owner_id":    scan.OwnerID,
		"status":      scan.Status,
		"risk_level":  scan.RiskLevel,
		"action":      scan.Action,
		"categories":  jsonAny(scan.CategoriesJSON),
		"created_at":  scan.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":  scan.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func VendorProcessingRecordPayload(record VendorProcessingRecord) map[string]any {
	return map[string]any{
		"id":                  record.ID,
		"provider_type":       record.ProviderType,
		"provider_name":       record.ProviderName,
		"operation":           record.Operation,
		"entity_type":         record.EntityType,
		"entity_id":           record.EntityID,
		"status":              record.Status,
		"region":              record.Region,
		"data_classification": record.DataClassification,
		"input_hash":          record.InputHash,
		"output_hash":         record.OutputHash,
		"prompt_version":      record.PromptVersion,
		"model_name":          record.ModelName,
		"latency_ms":          record.LatencyMS,
		"input_tokens":        record.InputTokens,
		"output_tokens":       record.OutputTokens,
		"cost_micros":         record.CostMicros,
		"error_code":          record.ErrorCode,
		"metadata":            jsonMap(record.MetadataJSON),
		"created_at":          record.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func VendorProcessingRecordsPayload(records []VendorProcessingRecord) []map[string]any {
	items := make([]map[string]any, 0, len(records))
	for _, record := range records {
		items = append(items, VendorProcessingRecordPayload(record))
	}
	return items
}

func PayoutProfilePayload(profile PayoutProfile) map[string]any {
	return map[string]any{
		"id":                 profile.ID,
		"contributor_id":     profile.ContributorID,
		"status":             profile.Status,
		"country_region":     profile.CountryRegion,
		"account_type":       profile.AccountType,
		"account_ref_suffix": profile.AccountRefSuffix,
		"account_ref_hash":   profile.AccountRefHash,
		"kyc_status":         profile.KYCStatus,
		"tax_status":         profile.TaxStatus,
		"risk_status":        profile.RiskStatus,
		"created_at":         profile.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":         profile.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func AuthorizationWithdrawalPayload(withdrawal AuthorizationWithdrawal) map[string]any {
	return map[string]any{
		"id":               withdrawal.ID,
		"authorization_id": withdrawal.AuthorizationID,
		"status":           withdrawal.Status,
		"withdrawn_at":     timePtrString(withdrawal.WithdrawnAt),
		"created_at":       withdrawal.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":       withdrawal.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func scanReviewSample(scanner interface{ Scan(dest ...any) error }) (ReviewSample, error) {
	var sample ReviewSample
	err := scanner.Scan(&sample.ID, &sample.CaseID, &sample.SampleType, &sample.Status, &sample.Blind, &sample.Decision, &sample.Score, &sample.MinDRL, &sample.Reason, &sample.Notes, &sample.PayloadJSON, &sample.CreatedAt, &sample.UpdatedAt)
	return sample, err
}

func scanDatasetEvaluation(scanner interface{ Scan(dest ...any) error }) (DatasetEvaluation, error) {
	var evaluation DatasetEvaluation
	err := scanner.Scan(&evaluation.ID, &evaluation.DatasetID, &evaluation.Status, &evaluation.MetricsJSON, &evaluation.FindingsJSON, &evaluation.CreatedAt, &evaluation.UpdatedAt)
	return evaluation, err
}

func scanInvoice(scanner interface{ Scan(dest ...any) error }) (Invoice, error) {
	var invoice Invoice
	err := scanner.Scan(&invoice.ID, &invoice.OrderID, &invoice.InvoiceNoSuffix, &invoice.Status, &invoice.AmountCents, &invoice.TaxCents, &invoice.CreatedAt, &invoice.UpdatedAt)
	return invoice, err
}

func scanInbox(scanner interface{ Scan(dest ...any) error }) (Inbox, error) {
	var inbox Inbox
	err := scanner.Scan(&inbox.ID, &inbox.OwnerID, &inbox.Address, &inbox.Status, &inbox.AllowedUsesJSON, &inbox.CreatedAt, &inbox.UpdatedAt)
	return inbox, err
}

func scanPayoutProfile(scanner interface{ Scan(dest ...any) error }) (PayoutProfile, error) {
	var profile PayoutProfile
	err := scanner.Scan(&profile.ID, &profile.ContributorID, &profile.Status, &profile.CountryRegion, &profile.AccountType, &profile.AccountRefSuffix, &profile.AccountRefHash, &profile.KYCStatus, &profile.TaxStatus, &profile.RiskStatus, &profile.CreatedAt, &profile.UpdatedAt)
	return profile, err
}

func scanContentSafetyScan(scanner interface{ Scan(dest ...any) error }) (ContentSafetyScan, error) {
	var scan ContentSafetyScan
	err := scanner.Scan(&scan.ID, &scan.EntityType, &scan.EntityID, &scan.OwnerID, &scan.Status, &scan.RiskLevel, &scan.Action, &scan.CategoriesJSON, &scan.CreatedAt, &scan.UpdatedAt)
	return scan, err
}

func scanVendorProcessingRecord(scanner interface{ Scan(dest ...any) error }) (VendorProcessingRecord, error) {
	var record VendorProcessingRecord
	err := scanner.Scan(
		&record.ID,
		&record.ProviderType,
		&record.ProviderName,
		&record.Operation,
		&record.EntityType,
		&record.EntityID,
		&record.Status,
		&record.Region,
		&record.DataClassification,
		&record.InputHash,
		&record.OutputHash,
		&record.PromptVersion,
		&record.ModelName,
		&record.LatencyMS,
		&record.InputTokens,
		&record.OutputTokens,
		&record.CostMicros,
		&record.ErrorCode,
		&record.MetadataJSON,
		&record.CreatedAt,
	)
	return record, err
}

func jsonAny(raw string) any {
	var out any
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		return nil
	}
	return out
}

var typedOperationalSchemaStatements = []string{
	`CREATE TABLE IF NOT EXISTS disputes (
		id VARCHAR(64) PRIMARY KEY,
		entity_type VARCHAR(64) NOT NULL,
		entity_id VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		held_payout_count INT NOT NULL DEFAULT 0,
		reason VARCHAR(1200) NOT NULL DEFAULT '',
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_disputes_entity_created (entity_type, entity_id, created_at),
		KEY idx_disputes_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS review_samples (
		id VARCHAR(64) PRIMARY KEY,
		case_id VARCHAR(64) NOT NULL,
		sample_type VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		blind BOOLEAN NOT NULL DEFAULT TRUE,
		decision VARCHAR(64) NOT NULL DEFAULT '',
		score DOUBLE NOT NULL DEFAULT 0,
		min_drl VARCHAR(16) NOT NULL DEFAULT '',
		reason VARCHAR(1200) NOT NULL DEFAULT '',
		notes TEXT NULL,
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_review_samples_case_created (case_id, created_at),
		KEY idx_review_samples_status_created (status, created_at),
		KEY idx_review_samples_type_status (sample_type, status)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS dataset_evaluations (
		id VARCHAR(64) PRIMARY KEY,
		dataset_id VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		metrics_json JSON NOT NULL,
		findings_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_dataset_evaluations_dataset_created (dataset_id, created_at),
		KEY idx_dataset_evaluations_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS reconciliation_reports (
		id VARCHAR(64) PRIMARY KEY,
		scope_type VARCHAR(64) NOT NULL,
		scope_id VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		summary_json JSON NOT NULL,
		anomalies_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_reconciliation_scope_created (scope_type, scope_id, created_at),
		KEY idx_reconciliation_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS invoices (
		id VARCHAR(64) PRIMARY KEY,
		order_id VARCHAR(64) NOT NULL,
		invoice_no_suffix VARCHAR(32) NOT NULL DEFAULT '',
		status VARCHAR(32) NOT NULL,
		amount_cents BIGINT NOT NULL DEFAULT 0,
		tax_cents BIGINT NOT NULL DEFAULT 0,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_invoices_order_created (order_id, created_at),
		KEY idx_invoices_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS sso_providers (
		id VARCHAR(64) PRIMARY KEY,
		tenant_id VARCHAR(128) NOT NULL,
		provider_type VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		domain VARCHAR(255) NOT NULL DEFAULT '',
		issuer VARCHAR(500) NOT NULL DEFAULT '',
		metadata_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_sso_providers_tenant_status (tenant_id, status),
		KEY idx_sso_providers_domain (domain)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS inboxes (
		id VARCHAR(64) PRIMARY KEY,
		owner_id VARCHAR(128) NOT NULL,
		address VARCHAR(255) NOT NULL,
		status VARCHAR(32) NOT NULL,
		allowed_uses_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		UNIQUE KEY uq_inboxes_address (address),
		KEY idx_inboxes_owner_created (owner_id, created_at),
		KEY idx_inboxes_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS inbound_messages (
		id VARCHAR(64) PRIMARY KEY,
		inbox_id VARCHAR(64) NULL,
		owner_id VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		subject_hash CHAR(12) NOT NULL DEFAULT '',
		subject_length INT NOT NULL DEFAULT 0,
		submission_id VARCHAR(64) NOT NULL,
		message_id_hash CHAR(12) NOT NULL DEFAULT '',
		sender_domain VARCHAR(255) NOT NULL DEFAULT '',
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_inbound_messages_inbox_created (inbox_id, created_at),
		KEY idx_inbound_messages_owner_created (owner_id, created_at),
		KEY idx_inbound_messages_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS webhook_cases (
		id VARCHAR(64) PRIMARY KEY,
		source VARCHAR(64) NOT NULL,
		owner_id VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		submission_id VARCHAR(64) NOT NULL,
		external_id_hash CHAR(12) NOT NULL DEFAULT '',
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_webhook_cases_source_created (source, created_at),
		KEY idx_webhook_cases_owner_created (owner_id, created_at),
		KEY idx_webhook_cases_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS content_safety_scans (
		id VARCHAR(64) PRIMARY KEY,
		entity_type VARCHAR(64) NOT NULL,
		entity_id VARCHAR(128) NOT NULL,
		owner_id VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		risk_level VARCHAR(32) NOT NULL,
		action VARCHAR(64) NOT NULL,
		categories_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_content_safety_entity_created (entity_type, entity_id, created_at),
		KEY idx_content_safety_owner_created (owner_id, created_at),
		KEY idx_content_safety_risk_created (risk_level, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS payout_profiles (
		id VARCHAR(64) PRIMARY KEY,
		contributor_id VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		country_region VARCHAR(32) NOT NULL DEFAULT '',
		account_type VARCHAR(64) NOT NULL DEFAULT '',
		account_ref_suffix VARCHAR(32) NOT NULL DEFAULT '',
		account_ref_hash CHAR(12) NOT NULL DEFAULT '',
		kyc_status VARCHAR(32) NOT NULL DEFAULT '',
		tax_status VARCHAR(32) NOT NULL DEFAULT '',
		risk_status VARCHAR(32) NOT NULL DEFAULT '',
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_payout_profiles_contributor_created (contributor_id, created_at),
		KEY idx_payout_profiles_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS authorization_withdrawals (
		id VARCHAR(64) PRIMARY KEY,
		authorization_id VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		withdrawn_at DATETIME(6) NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_authorization_withdrawals_auth_status (authorization_id, status),
		KEY idx_authorization_withdrawals_created (created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
}

var typedModelGatewaySchemaStatements = []string{
	`CREATE TABLE IF NOT EXISTS vendor_processing_records (
		id VARCHAR(64) PRIMARY KEY,
		provider_type VARCHAR(64) NOT NULL,
		provider_name VARCHAR(128) NOT NULL,
		operation VARCHAR(128) NOT NULL,
		entity_type VARCHAR(64) NOT NULL,
		entity_id VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		region VARCHAR(64) NOT NULL DEFAULT '',
		data_classification VARCHAR(64) NOT NULL DEFAULT '',
		input_hash CHAR(64) NOT NULL DEFAULT '',
		output_hash CHAR(64) NOT NULL DEFAULT '',
		prompt_version VARCHAR(128) NOT NULL DEFAULT '',
		model_name VARCHAR(128) NOT NULL DEFAULT '',
		latency_ms BIGINT NOT NULL DEFAULT 0,
		input_tokens INT NOT NULL DEFAULT 0,
		output_tokens INT NOT NULL DEFAULT 0,
		cost_micros BIGINT NOT NULL DEFAULT 0,
		error_code VARCHAR(128) NOT NULL DEFAULT '',
		metadata_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		KEY idx_vendor_records_entity_created (entity_type, entity_id, created_at),
		KEY idx_vendor_records_provider_created (provider_type, provider_name, created_at),
		KEY idx_vendor_records_status_created (status, created_at),
		KEY idx_vendor_records_operation_created (operation, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
}
