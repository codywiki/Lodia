package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"
)

type PayoutBatch struct {
	ID               string
	Status           string
	PayoutCount      int
	TotalAmountCents int64
	MinAmountCents   int64
	MaxEvents        int
	SettledAt        *time.Time
	PayloadJSON      string
	CreatedAt        time.Time
	UpdatedAt        time.Time
}

type PayoutTransfer struct {
	ID                      string
	BatchID                 string
	ProviderName            string
	Status                  string
	AmountCents             int64
	ExternalReferenceSuffix string
	ExternalReferenceHash   string
	ResponseHash            string
	PayloadJSON             string
	CreatedAt               time.Time
	UpdatedAt               time.Time
}

type EnterpriseCustomer struct {
	ID                 string
	TenantID           string
	Name               string
	Status             string
	ContactEmailDomain string
	CreatedAt          time.Time
	UpdatedAt          time.Time
}

type EnterpriseContract struct {
	ID         string
	CustomerID string
	Status     string
	Version    string
	ExpiresAt  *time.Time
	TermsJSON  string
	CreatedAt  time.Time
	UpdatedAt  time.Time
}

type EnterpriseOrder struct {
	ID                string
	CustomerID        string
	DatasetID         string
	ContractID        string
	Status            string
	GrossRevenueCents int64
	DirectCostCents   int64
	MaxReads          int
	UsageEventID      string
	DeliveryGrantID   string
	CreatedAt         time.Time
	UpdatedAt         time.Time
}

type DeliveryGrant struct {
	ID          string
	OrderID     string
	DatasetID   string
	CustomerID  string
	Status      string
	TokenSuffix string
	TokenHash   string
	ReadCount   int
	MaxReads    int
	ExpiresAt   *time.Time
	CreatedAt   time.Time
	UpdatedAt   time.Time
}

type BuyerUsageReport struct {
	ID                string
	GrantID           string
	Status            string
	ReportedCaseCount int
	ExternalEventHash string
	PayloadJSON       string
	CreatedAt         time.Time
	UpdatedAt         time.Time
}

type DSRRequest struct {
	ID            string
	OwnerID       string
	RequestType   string
	Status        string
	DeletedCases  int
	DeletedAssets int
	Reason        string
	FulfilledAt   *time.Time
	CreatedAt     time.Time
	UpdatedAt     time.Time
}

type ProviderConfig struct {
	ID           string
	ProviderType string
	ProviderName string
	Status       string
	Mode         string
	Region       string
	PayloadJSON  string
	CreatedAt    time.Time
	UpdatedAt    time.Time
}

type ComplianceTask struct {
	ID          string
	TaskType    string
	Status      string
	Title       string
	PayloadJSON string
	CreatedAt   time.Time
	UpdatedAt   time.Time
}

func (db *DB) CreatePayoutBatch(ctx context.Context, batch *PayoutBatch) error {
	now := nowUTC()
	if batch.ID == "" {
		batch.ID = NewID("batch")
	}
	if batch.Status == "" {
		batch.Status = "ready"
	}
	if batch.PayloadJSON == "" {
		batch.PayloadJSON = "{}"
	}
	batch.CreatedAt = now
	batch.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO payout_batches
			(id, status, payout_count, total_amount_cents, min_amount_cents, max_events, settled_at, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, batch.ID, batch.Status, batch.PayoutCount, batch.TotalAmountCents, batch.MinAmountCents, batch.MaxEvents, batch.SettledAt, batch.PayloadJSON, batch.CreatedAt, batch.UpdatedAt)
	return err
}

func (db *DB) GetPayoutBatch(ctx context.Context, id string) (PayoutBatch, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, status, payout_count, total_amount_cents, min_amount_cents, max_events, settled_at, CAST(payload_json AS CHAR), created_at, updated_at
		FROM payout_batches WHERE id = ?
	`, id)
	return scanPayoutBatch(row)
}

func (db *DB) SettlePayoutBatch(ctx context.Context, id string) (PayoutBatch, error) {
	now := nowUTC()
	_, err := db.sql.ExecContext(ctx, `
		UPDATE payout_batches SET status = 'settled', settled_at = COALESCE(settled_at, ?), updated_at = ? WHERE id = ? AND status <> 'settled'
	`, now, now, id)
	if err != nil {
		return PayoutBatch{}, err
	}
	return db.GetPayoutBatch(ctx, id)
}

func (db *DB) CreatePayoutTransfer(ctx context.Context, transfer *PayoutTransfer) error {
	now := nowUTC()
	if transfer.ID == "" {
		transfer.ID = NewID("transfer")
	}
	if transfer.Status == "" {
		transfer.Status = "submitted"
	}
	if transfer.PayloadJSON == "" {
		transfer.PayloadJSON = "{}"
	}
	transfer.CreatedAt = now
	transfer.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO payout_transfers
			(id, batch_id, provider_name, status, amount_cents, external_reference_suffix, external_reference_hash, response_hash, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, transfer.ID, transfer.BatchID, transfer.ProviderName, transfer.Status, transfer.AmountCents, transfer.ExternalReferenceSuffix, transfer.ExternalReferenceHash, transfer.ResponseHash, transfer.PayloadJSON, transfer.CreatedAt, transfer.UpdatedAt)
	return err
}

func (db *DB) GetPayoutTransfer(ctx context.Context, id string) (PayoutTransfer, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, batch_id, provider_name, status, amount_cents, COALESCE(external_reference_suffix, ''), COALESCE(external_reference_hash, ''), COALESCE(response_hash, ''), CAST(payload_json AS CHAR), created_at, updated_at
		FROM payout_transfers WHERE id = ?
	`, id)
	return scanPayoutTransfer(row)
}

func (db *DB) ConfirmPayoutTransfer(ctx context.Context, id string, status string, externalReferenceSuffix string, externalReferenceHash string, responseHash string, payload any) (PayoutTransfer, error) {
	payloadJSON, err := jsonText(payload)
	if err != nil {
		return PayoutTransfer{}, err
	}
	if status == "" {
		status = "succeeded"
	}
	_, err = db.sql.ExecContext(ctx, `
		UPDATE payout_transfers
		SET status = ?, external_reference_suffix = ?, external_reference_hash = ?, response_hash = ?, payload_json = ?, updated_at = ?
		WHERE id = ?
	`, status, externalReferenceSuffix, externalReferenceHash, responseHash, payloadJSON, nowUTC(), id)
	if err != nil {
		return PayoutTransfer{}, err
	}
	return db.GetPayoutTransfer(ctx, id)
}

func (db *DB) CountPayoutTransfers(ctx context.Context, batchID string, status string) (int64, error) {
	query := `SELECT COUNT(*) FROM payout_transfers WHERE batch_id = ?`
	args := []any{batchID}
	if status != "" {
		query += ` AND status = ?`
		args = append(args, status)
	}
	var count int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&count)
	return count, err
}

func (db *DB) SumPayoutTransfers(ctx context.Context, batchID string, status string) (int64, error) {
	query := `SELECT COALESCE(SUM(amount_cents), 0) FROM payout_transfers WHERE batch_id = ?`
	args := []any{batchID}
	if status != "" {
		query += ` AND status = ?`
		args = append(args, status)
	}
	var total int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&total)
	return total, err
}

func (db *DB) MissingActivePayoutProfiles(ctx context.Context, batchID string) ([]string, error) {
	rows, err := db.sql.QueryContext(ctx, `
		SELECT pe.contributor_id
		FROM payout_events pe
		LEFT JOIN payout_profiles pp ON pp.contributor_id = pe.contributor_id AND pp.status = 'active'
		WHERE pe.batch_id = ?
		GROUP BY pe.contributor_id
		HAVING COUNT(pp.id) = 0
		ORDER BY pe.contributor_id ASC
	`, batchID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	missing := []string{}
	for rows.Next() {
		var contributorID string
		if err := rows.Scan(&contributorID); err != nil {
			return nil, err
		}
		missing = append(missing, contributorID)
	}
	return missing, rows.Err()
}

func (db *DB) CreateEnterpriseCustomer(ctx context.Context, customer *EnterpriseCustomer) error {
	now := nowUTC()
	if customer.ID == "" {
		customer.ID = NewID("cust")
	}
	if customer.Status == "" {
		customer.Status = "active"
	}
	customer.CreatedAt = now
	customer.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO enterprise_customers (id, tenant_id, name, status, contact_email_domain, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, customer.ID, customer.TenantID, customer.Name, customer.Status, customer.ContactEmailDomain, customer.CreatedAt, customer.UpdatedAt)
	return err
}

func (db *DB) ListEnterpriseCustomers(ctx context.Context, limit int) ([]EnterpriseCustomer, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, tenant_id, name, status, contact_email_domain, created_at, updated_at
		FROM enterprise_customers ORDER BY created_at DESC LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	customers := []EnterpriseCustomer{}
	for rows.Next() {
		var customer EnterpriseCustomer
		if err := rows.Scan(&customer.ID, &customer.TenantID, &customer.Name, &customer.Status, &customer.ContactEmailDomain, &customer.CreatedAt, &customer.UpdatedAt); err != nil {
			return nil, err
		}
		customers = append(customers, customer)
	}
	return customers, rows.Err()
}

func (db *DB) CreateEnterpriseContract(ctx context.Context, contract *EnterpriseContract) error {
	now := nowUTC()
	if contract.ID == "" {
		contract.ID = NewID("contract")
	}
	if contract.Status == "" {
		contract.Status = "active"
	}
	if contract.TermsJSON == "" {
		contract.TermsJSON = "{}"
	}
	contract.CreatedAt = now
	contract.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO enterprise_contracts (id, customer_id, status, version, expires_at, terms_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, contract.ID, contract.CustomerID, contract.Status, contract.Version, contract.ExpiresAt, contract.TermsJSON, contract.CreatedAt, contract.UpdatedAt)
	return err
}

func (db *DB) CreateEnterpriseOrder(ctx context.Context, order *EnterpriseOrder) error {
	now := nowUTC()
	if order.ID == "" {
		order.ID = NewID("order")
	}
	if order.Status == "" {
		order.Status = "created"
	}
	order.CreatedAt = now
	order.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO enterprise_orders
			(id, customer_id, dataset_id, contract_id, status, gross_revenue_cents, direct_cost_cents, max_reads, usage_event_id, delivery_grant_id, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''), NULLIF(?, ''), ?, ?)
	`, order.ID, order.CustomerID, order.DatasetID, order.ContractID, order.Status, order.GrossRevenueCents, order.DirectCostCents, order.MaxReads, order.UsageEventID, order.DeliveryGrantID, order.CreatedAt, order.UpdatedAt)
	return err
}

func (db *DB) GetEnterpriseOrder(ctx context.Context, id string) (EnterpriseOrder, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, customer_id, dataset_id, contract_id, status, gross_revenue_cents, direct_cost_cents, max_reads, COALESCE(usage_event_id, ''), COALESCE(delivery_grant_id, ''), created_at, updated_at
		FROM enterprise_orders WHERE id = ?
	`, id)
	return scanEnterpriseOrder(row)
}

func (db *DB) RecognizeEnterpriseOrder(ctx context.Context, id string, usageEventID string) (EnterpriseOrder, error) {
	_, err := db.sql.ExecContext(ctx, `
		UPDATE enterprise_orders SET status = 'revenue_recognized', usage_event_id = NULLIF(?, ''), updated_at = ? WHERE id = ?
	`, usageEventID, nowUTC(), id)
	if err != nil {
		return EnterpriseOrder{}, err
	}
	return db.GetEnterpriseOrder(ctx, id)
}

func (db *DB) LinkEnterpriseOrderGrant(ctx context.Context, id string, grantID string) error {
	_, err := db.sql.ExecContext(ctx, `
		UPDATE enterprise_orders SET delivery_grant_id = NULLIF(?, ''), updated_at = ? WHERE id = ?
	`, grantID, nowUTC(), id)
	return err
}

func (db *DB) CreateDeliveryGrant(ctx context.Context, grant *DeliveryGrant) error {
	now := nowUTC()
	if grant.ID == "" {
		grant.ID = NewID("grant")
	}
	if grant.Status == "" {
		grant.Status = "active"
	}
	grant.CreatedAt = now
	grant.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO delivery_grants
			(id, order_id, dataset_id, customer_id, status, token_suffix, token_hash, read_count, max_reads, expires_at, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, grant.ID, grant.OrderID, grant.DatasetID, grant.CustomerID, grant.Status, grant.TokenSuffix, grant.TokenHash, grant.ReadCount, grant.MaxReads, grant.ExpiresAt, grant.CreatedAt, grant.UpdatedAt)
	return err
}

func (db *DB) GetDeliveryGrant(ctx context.Context, id string) (DeliveryGrant, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, order_id, dataset_id, customer_id, status, token_suffix, token_hash, read_count, max_reads, expires_at, created_at, updated_at
		FROM delivery_grants WHERE id = ?
	`, id)
	return scanDeliveryGrant(row)
}

func (db *DB) IncrementDeliveryGrantRead(ctx context.Context, id string) (DeliveryGrant, error) {
	result, err := db.sql.ExecContext(ctx, `
		UPDATE delivery_grants SET read_count = read_count + 1, updated_at = ? WHERE id = ? AND read_count < max_reads
	`, nowUTC(), id)
	if err != nil {
		return DeliveryGrant{}, err
	}
	if rows, err := result.RowsAffected(); err == nil && rows == 0 {
		return DeliveryGrant{}, fmt.Errorf("delivery_read_limit_exceeded")
	}
	return db.GetDeliveryGrant(ctx, id)
}

func (db *DB) CreateBuyerUsageReport(ctx context.Context, report *BuyerUsageReport) error {
	now := nowUTC()
	if report.ID == "" {
		report.ID = NewID("usage")
	}
	if report.Status == "" {
		report.Status = "recorded"
	}
	if report.PayloadJSON == "" {
		report.PayloadJSON = "{}"
	}
	report.CreatedAt = now
	report.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO buyer_usage_reports
			(id, grant_id, status, reported_case_count, external_event_hash, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, report.ID, report.GrantID, report.Status, report.ReportedCaseCount, report.ExternalEventHash, report.PayloadJSON, report.CreatedAt, report.UpdatedAt)
	return err
}

func (db *DB) ListBuyerUsageReportsByGrant(ctx context.Context, grantID string, limit int) ([]BuyerUsageReport, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, grant_id, status, reported_case_count, external_event_hash, CAST(payload_json AS CHAR), created_at, updated_at
		FROM buyer_usage_reports WHERE grant_id = ? ORDER BY created_at DESC LIMIT ?
	`, grantID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	reports := []BuyerUsageReport{}
	for rows.Next() {
		report, err := scanBuyerUsageReport(rows)
		if err != nil {
			return nil, err
		}
		reports = append(reports, report)
	}
	return reports, rows.Err()
}

func (db *DB) CreateDSRRequest(ctx context.Context, request *DSRRequest) error {
	now := nowUTC()
	if request.ID == "" {
		request.ID = NewID("dsr")
	}
	if request.Status == "" {
		request.Status = "open"
	}
	request.CreatedAt = now
	request.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO dsr_requests
			(id, owner_id, request_type, status, deleted_cases, deleted_assets, reason, fulfilled_at, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, request.ID, request.OwnerID, request.RequestType, request.Status, request.DeletedCases, request.DeletedAssets, request.Reason, request.FulfilledAt, request.CreatedAt, request.UpdatedAt)
	return err
}

func (db *DB) GetDSRRequest(ctx context.Context, id string) (DSRRequest, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, owner_id, request_type, status, deleted_cases, deleted_assets, reason, fulfilled_at, created_at, updated_at
		FROM dsr_requests WHERE id = ?
	`, id)
	return scanDSRRequest(row)
}

func (db *DB) FulfillDSRRequest(ctx context.Context, id string) (DSRRequest, error) {
	now := nowUTC()
	_, err := db.sql.ExecContext(ctx, `
		UPDATE dsr_requests SET status = 'fulfilled', fulfilled_at = COALESCE(fulfilled_at, ?), updated_at = ? WHERE id = ?
	`, now, now, id)
	if err != nil {
		return DSRRequest{}, err
	}
	return db.GetDSRRequest(ctx, id)
}

func (db *DB) FulfillDSRRequestWithEvidence(ctx context.Context, id string, deletedCases int, deletedAssets int) (DSRRequest, error) {
	now := nowUTC()
	_, err := db.sql.ExecContext(ctx, `
		UPDATE dsr_requests
		SET status = 'fulfilled', deleted_cases = ?, deleted_assets = ?, fulfilled_at = COALESCE(fulfilled_at, ?), updated_at = ?
		WHERE id = ?
	`, deletedCases, deletedAssets, now, now, id)
	if err != nil {
		return DSRRequest{}, err
	}
	return db.GetDSRRequest(ctx, id)
}

func (db *DB) CreateProviderConfig(ctx context.Context, provider *ProviderConfig) error {
	now := nowUTC()
	if provider.ID == "" {
		provider.ID = NewID("provider")
	}
	if provider.Status == "" {
		provider.Status = "testing"
	}
	if provider.PayloadJSON == "" {
		provider.PayloadJSON = "{}"
	}
	provider.CreatedAt = now
	provider.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO provider_configs (id, provider_type, provider_name, status, mode, region, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, provider.ID, provider.ProviderType, provider.ProviderName, provider.Status, provider.Mode, provider.Region, provider.PayloadJSON, provider.CreatedAt, provider.UpdatedAt)
	return err
}

func (db *DB) CountProviderConfigs(ctx context.Context, status string) (int64, error) {
	query := `SELECT COUNT(*) FROM provider_configs`
	args := []any{}
	if status != "" {
		query += ` WHERE status = ?`
		args = append(args, status)
	}
	var count int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&count)
	return count, err
}

func (db *DB) CreateComplianceTask(ctx context.Context, task *ComplianceTask) error {
	now := nowUTC()
	if task.ID == "" {
		task.ID = NewID("compliance")
	}
	if task.Status == "" {
		task.Status = "open"
	}
	if task.PayloadJSON == "" {
		task.PayloadJSON = "{}"
	}
	task.CreatedAt = now
	task.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO compliance_tasks (id, task_type, status, title, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, task.ID, task.TaskType, task.Status, task.Title, task.PayloadJSON, task.CreatedAt, task.UpdatedAt)
	return err
}

func (db *DB) CountComplianceTasks(ctx context.Context, status string) (int64, error) {
	query := `SELECT COUNT(*) FROM compliance_tasks`
	args := []any{}
	if status != "" {
		query += ` WHERE status = ?`
		args = append(args, status)
	}
	var count int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&count)
	return count, err
}

func (db *DB) CompletedComplianceTaskTypes(ctx context.Context) (map[string]int64, error) {
	rows, err := db.sql.QueryContext(ctx, `
		SELECT task_type, COUNT(*) FROM compliance_tasks WHERE status = 'completed' GROUP BY task_type
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]int64{}
	for rows.Next() {
		var taskType string
		var count int64
		if err := rows.Scan(&taskType, &count); err != nil {
			return nil, err
		}
		out[taskType] = count
	}
	return out, rows.Err()
}

func PayoutBatchPayload(batch PayoutBatch) map[string]any {
	return map[string]any{
		"id":                 batch.ID,
		"status":             batch.Status,
		"payout_count":       batch.PayoutCount,
		"total_amount_cents": batch.TotalAmountCents,
		"min_amount_cents":   batch.MinAmountCents,
		"max_events":         batch.MaxEvents,
		"settled_at":         timePtrString(batch.SettledAt),
		"created_at":         batch.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":         batch.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func PayoutTransferPayload(transfer PayoutTransfer) map[string]any {
	return map[string]any{
		"id":                        transfer.ID,
		"batch_id":                  transfer.BatchID,
		"provider_name":             transfer.ProviderName,
		"status":                    transfer.Status,
		"amount_cents":              transfer.AmountCents,
		"external_reference_suffix": transfer.ExternalReferenceSuffix,
		"external_reference_hash":   transfer.ExternalReferenceHash,
		"response_hash":             transfer.ResponseHash,
		"created_at":                transfer.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":                transfer.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func EnterpriseCustomerPayload(customer EnterpriseCustomer) map[string]any {
	return map[string]any{
		"id":                   customer.ID,
		"tenant_id":            customer.TenantID,
		"name":                 customer.Name,
		"status":               customer.Status,
		"contact_email_domain": customer.ContactEmailDomain,
		"created_at":           customer.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":           customer.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func EnterpriseContractPayload(contract EnterpriseContract) map[string]any {
	return map[string]any{
		"id":          contract.ID,
		"customer_id": contract.CustomerID,
		"status":      contract.Status,
		"version":     contract.Version,
		"expires_at":  timePtrString(contract.ExpiresAt),
		"terms":       jsonMap(contract.TermsJSON),
		"created_at":  contract.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":  contract.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func EnterpriseOrderPayload(order EnterpriseOrder) map[string]any {
	return map[string]any{
		"id":                  order.ID,
		"customer_id":         order.CustomerID,
		"dataset_id":          order.DatasetID,
		"contract_id":         order.ContractID,
		"status":              order.Status,
		"gross_revenue_cents": order.GrossRevenueCents,
		"direct_cost_cents":   order.DirectCostCents,
		"max_reads":           order.MaxReads,
		"usage_event_id":      order.UsageEventID,
		"delivery_grant_id":   order.DeliveryGrantID,
		"created_at":          order.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":          order.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func DeliveryGrantPayload(grant DeliveryGrant) map[string]any {
	return map[string]any{
		"id":           grant.ID,
		"order_id":     grant.OrderID,
		"dataset_id":   grant.DatasetID,
		"customer_id":  grant.CustomerID,
		"status":       grant.Status,
		"token_suffix": grant.TokenSuffix,
		"token_hash":   grant.TokenHash,
		"read_count":   grant.ReadCount,
		"max_reads":    grant.MaxReads,
		"expires_at":   timePtrString(grant.ExpiresAt),
		"created_at":   grant.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":   grant.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func BuyerUsageReportPayload(report BuyerUsageReport) map[string]any {
	return map[string]any{
		"id":                  report.ID,
		"grant_id":            report.GrantID,
		"status":              report.Status,
		"reported_case_count": report.ReportedCaseCount,
		"external_event_hash": report.ExternalEventHash,
		"payload":             jsonMap(report.PayloadJSON),
		"created_at":          report.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":          report.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func DSRRequestPayload(request DSRRequest) map[string]any {
	return map[string]any{
		"id":             request.ID,
		"owner_id":       request.OwnerID,
		"request_type":   request.RequestType,
		"status":         request.Status,
		"deleted_cases":  request.DeletedCases,
		"deleted_assets": request.DeletedAssets,
		"reason":         request.Reason,
		"fulfilled_at":   timePtrString(request.FulfilledAt),
		"created_at":     request.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":     request.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func ProviderConfigPayload(provider ProviderConfig) map[string]any {
	return map[string]any{
		"id":            provider.ID,
		"provider_type": provider.ProviderType,
		"provider_name": provider.ProviderName,
		"status":        provider.Status,
		"mode":          provider.Mode,
		"region":        provider.Region,
		"payload":       jsonMap(provider.PayloadJSON),
		"created_at":    provider.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":    provider.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func ComplianceTaskPayload(task ComplianceTask) map[string]any {
	return map[string]any{
		"id":         task.ID,
		"task_type":  task.TaskType,
		"status":     task.Status,
		"title":      task.Title,
		"payload":    jsonMap(task.PayloadJSON),
		"created_at": task.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at": task.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func EnterpriseCustomersPayload(customers []EnterpriseCustomer) []map[string]any {
	out := make([]map[string]any, 0, len(customers))
	for _, customer := range customers {
		out = append(out, EnterpriseCustomerPayload(customer))
	}
	return out
}

func BuyerUsageReportsPayload(reports []BuyerUsageReport) []map[string]any {
	out := make([]map[string]any, 0, len(reports))
	for _, report := range reports {
		out = append(out, BuyerUsageReportPayload(report))
	}
	return out
}

func scanPayoutBatch(scanner interface{ Scan(dest ...any) error }) (PayoutBatch, error) {
	var batch PayoutBatch
	var settled sql.NullTime
	if err := scanner.Scan(&batch.ID, &batch.Status, &batch.PayoutCount, &batch.TotalAmountCents, &batch.MinAmountCents, &batch.MaxEvents, &settled, &batch.PayloadJSON, &batch.CreatedAt, &batch.UpdatedAt); err != nil {
		return PayoutBatch{}, err
	}
	if settled.Valid {
		batch.SettledAt = &settled.Time
	}
	return batch, nil
}

func scanPayoutTransfer(scanner interface{ Scan(dest ...any) error }) (PayoutTransfer, error) {
	var transfer PayoutTransfer
	err := scanner.Scan(&transfer.ID, &transfer.BatchID, &transfer.ProviderName, &transfer.Status, &transfer.AmountCents, &transfer.ExternalReferenceSuffix, &transfer.ExternalReferenceHash, &transfer.ResponseHash, &transfer.PayloadJSON, &transfer.CreatedAt, &transfer.UpdatedAt)
	return transfer, err
}

func scanEnterpriseOrder(scanner interface{ Scan(dest ...any) error }) (EnterpriseOrder, error) {
	var order EnterpriseOrder
	err := scanner.Scan(&order.ID, &order.CustomerID, &order.DatasetID, &order.ContractID, &order.Status, &order.GrossRevenueCents, &order.DirectCostCents, &order.MaxReads, &order.UsageEventID, &order.DeliveryGrantID, &order.CreatedAt, &order.UpdatedAt)
	return order, err
}

func scanDeliveryGrant(scanner interface{ Scan(dest ...any) error }) (DeliveryGrant, error) {
	var grant DeliveryGrant
	var expires sql.NullTime
	if err := scanner.Scan(&grant.ID, &grant.OrderID, &grant.DatasetID, &grant.CustomerID, &grant.Status, &grant.TokenSuffix, &grant.TokenHash, &grant.ReadCount, &grant.MaxReads, &expires, &grant.CreatedAt, &grant.UpdatedAt); err != nil {
		return DeliveryGrant{}, err
	}
	if expires.Valid {
		grant.ExpiresAt = &expires.Time
	}
	return grant, nil
}

func scanBuyerUsageReport(scanner interface{ Scan(dest ...any) error }) (BuyerUsageReport, error) {
	var report BuyerUsageReport
	err := scanner.Scan(&report.ID, &report.GrantID, &report.Status, &report.ReportedCaseCount, &report.ExternalEventHash, &report.PayloadJSON, &report.CreatedAt, &report.UpdatedAt)
	return report, err
}

func scanDSRRequest(scanner interface{ Scan(dest ...any) error }) (DSRRequest, error) {
	var request DSRRequest
	var fulfilled sql.NullTime
	if err := scanner.Scan(&request.ID, &request.OwnerID, &request.RequestType, &request.Status, &request.DeletedCases, &request.DeletedAssets, &request.Reason, &fulfilled, &request.CreatedAt, &request.UpdatedAt); err != nil {
		return DSRRequest{}, err
	}
	if fulfilled.Valid {
		request.FulfilledAt = &fulfilled.Time
	}
	return request, nil
}

func timePtrString(value *time.Time) any {
	if value == nil {
		return nil
	}
	return value.UTC().Format(time.RFC3339)
}

func jsonMap(raw string) map[string]any {
	out := map[string]any{}
	_ = json.Unmarshal([]byte(raw), &out)
	return out
}

var typedBusinessSchemaStatements = []string{
	`CREATE TABLE IF NOT EXISTS payout_batches (
		id VARCHAR(64) PRIMARY KEY,
		status VARCHAR(32) NOT NULL,
		payout_count INT NOT NULL DEFAULT 0,
		total_amount_cents BIGINT NOT NULL DEFAULT 0,
		min_amount_cents BIGINT NOT NULL DEFAULT 0,
		max_events INT NOT NULL DEFAULT 0,
		settled_at DATETIME(6) NULL,
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_payout_batches_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS payout_transfers (
		id VARCHAR(64) PRIMARY KEY,
		batch_id VARCHAR(64) NOT NULL,
		provider_name VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		amount_cents BIGINT NOT NULL DEFAULT 0,
		external_reference_suffix VARCHAR(32) NOT NULL DEFAULT '',
		external_reference_hash CHAR(12) NOT NULL DEFAULT '',
		response_hash CHAR(12) NOT NULL DEFAULT '',
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_payout_transfers_batch_created (batch_id, created_at),
		KEY idx_payout_transfers_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS enterprise_customers (
		id VARCHAR(64) PRIMARY KEY,
		tenant_id VARCHAR(128) NOT NULL,
		name VARCHAR(255) NOT NULL,
		status VARCHAR(32) NOT NULL,
		contact_email_domain VARCHAR(255) NOT NULL DEFAULT '',
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_enterprise_customers_status_created (status, created_at),
		KEY idx_enterprise_customers_tenant (tenant_id)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS enterprise_contracts (
		id VARCHAR(64) PRIMARY KEY,
		customer_id VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		version VARCHAR(128) NOT NULL,
		expires_at DATETIME(6) NULL,
		terms_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_enterprise_contracts_customer_created (customer_id, created_at),
		KEY idx_enterprise_contracts_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS enterprise_orders (
		id VARCHAR(64) PRIMARY KEY,
		customer_id VARCHAR(64) NOT NULL,
		dataset_id VARCHAR(64) NOT NULL,
		contract_id VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		gross_revenue_cents BIGINT NOT NULL DEFAULT 0,
		direct_cost_cents BIGINT NOT NULL DEFAULT 0,
		max_reads INT NOT NULL DEFAULT 0,
		usage_event_id VARCHAR(64) NULL,
		delivery_grant_id VARCHAR(64) NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_enterprise_orders_customer_created (customer_id, created_at),
		KEY idx_enterprise_orders_dataset_created (dataset_id, created_at),
		KEY idx_enterprise_orders_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS delivery_grants (
		id VARCHAR(64) PRIMARY KEY,
		order_id VARCHAR(64) NOT NULL,
		dataset_id VARCHAR(64) NOT NULL,
		customer_id VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		token_suffix VARCHAR(16) NOT NULL,
		token_hash CHAR(64) NOT NULL,
		read_count INT NOT NULL DEFAULT 0,
		max_reads INT NOT NULL DEFAULT 20,
		expires_at DATETIME(6) NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_delivery_grants_dataset_created (dataset_id, created_at),
		KEY idx_delivery_grants_order (order_id),
		KEY idx_delivery_grants_status_expires (status, expires_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS buyer_usage_reports (
		id VARCHAR(64) PRIMARY KEY,
		grant_id VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		reported_case_count INT NOT NULL DEFAULT 0,
		external_event_hash CHAR(12) NOT NULL DEFAULT '',
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_buyer_usage_reports_grant_created (grant_id, created_at),
		KEY idx_buyer_usage_reports_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS dsr_requests (
		id VARCHAR(64) PRIMARY KEY,
		owner_id VARCHAR(128) NOT NULL,
		request_type VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		deleted_cases INT NOT NULL DEFAULT 0,
		deleted_assets INT NOT NULL DEFAULT 0,
		reason VARCHAR(1200) NOT NULL DEFAULT '',
		fulfilled_at DATETIME(6) NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_dsr_requests_owner_created (owner_id, created_at),
		KEY idx_dsr_requests_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS provider_configs (
		id VARCHAR(64) PRIMARY KEY,
		provider_type VARCHAR(64) NOT NULL,
		provider_name VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		mode VARCHAR(32) NOT NULL,
		region VARCHAR(64) NOT NULL DEFAULT '',
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_provider_configs_type_status (provider_type, status),
		KEY idx_provider_configs_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS compliance_tasks (
		id VARCHAR(64) PRIMARY KEY,
		task_type VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		title VARCHAR(255) NOT NULL,
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_compliance_tasks_type_status (task_type, status),
		KEY idx_compliance_tasks_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
}
