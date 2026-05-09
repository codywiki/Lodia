package store

import (
	"context"
	"database/sql"
	"encoding/json"
)

type Record struct {
	ID          string
	RecordType  string
	Status      string
	OwnerID     string
	ParentID    string
	PayloadJSON string
	CreatedAt   string
	UpdatedAt   string
}

func (db *DB) CreateRecord(ctx context.Context, recordType string, status string, ownerID string, parentID string, payload any) (Record, error) {
	payloadJSON, err := jsonText(payload)
	if err != nil {
		return Record{}, err
	}
	now := nowUTC()
	record := Record{
		ID:          NewID(recordPrefix(recordType)),
		RecordType:  recordType,
		Status:      status,
		OwnerID:     ownerID,
		ParentID:    parentID,
		PayloadJSON: payloadJSON,
		CreatedAt:   now.Format(timeFormatMySQLCompatible),
		UpdatedAt:   now.Format(timeFormatMySQLCompatible),
	}
	_, err = db.sql.ExecContext(ctx, `
		INSERT INTO records (id, record_type, status, owner_id, parent_id, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, NULLIF(?, ''), NULLIF(?, ''), ?, ?, ?)
	`, record.ID, record.RecordType, record.Status, record.OwnerID, record.ParentID, record.PayloadJSON, now, now)
	return record, err
}

func (db *DB) GetRecord(ctx context.Context, id string) (Record, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, record_type, status, COALESCE(owner_id, ''), COALESCE(parent_id, ''), CAST(payload_json AS CHAR), created_at, updated_at
		FROM records WHERE id = ?
	`, id)
	return scanRecord(row)
}

func (db *DB) UpdateRecord(ctx context.Context, id string, status string, payload any) (Record, error) {
	payloadJSON, err := jsonText(payload)
	if err != nil {
		return Record{}, err
	}
	if status == "" {
		status = "updated"
	}
	_, err = db.sql.ExecContext(ctx, `
		UPDATE records SET status = ?, payload_json = ?, updated_at = ? WHERE id = ?
	`, status, payloadJSON, nowUTC(), id)
	if err != nil {
		return Record{}, err
	}
	return db.GetRecord(ctx, id)
}

func (db *DB) ListRecords(ctx context.Context, recordType string, limit int) ([]Record, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, record_type, status, COALESCE(owner_id, ''), COALESCE(parent_id, ''), CAST(payload_json AS CHAR), created_at, updated_at
		FROM records WHERE record_type = ? ORDER BY created_at DESC LIMIT ?
	`, recordType, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanRecords(rows)
}

func (db *DB) ListRecordsByParent(ctx context.Context, recordType string, parentID string, limit int) ([]Record, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, record_type, status, COALESCE(owner_id, ''), COALESCE(parent_id, ''), CAST(payload_json AS CHAR), created_at, updated_at
		FROM records WHERE record_type = ? AND parent_id = ? ORDER BY created_at DESC LIMIT ?
	`, recordType, parentID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanRecords(rows)
}

func (db *DB) ListRecordsByOwner(ctx context.Context, recordType string, ownerID string, limit int) ([]Record, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, record_type, status, COALESCE(owner_id, ''), COALESCE(parent_id, ''), CAST(payload_json AS CHAR), created_at, updated_at
		FROM records WHERE record_type = ? AND owner_id = ? ORDER BY created_at DESC LIMIT ?
	`, recordType, ownerID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanRecords(rows)
}

func (db *DB) CountRecords(ctx context.Context, recordType string, status string) (int64, error) {
	if status == "" {
		var count int64
		err := db.sql.QueryRowContext(ctx, `SELECT COUNT(*) FROM records WHERE record_type = ?`, recordType).Scan(&count)
		return count, err
	}
	var count int64
	err := db.sql.QueryRowContext(ctx, `SELECT COUNT(*) FROM records WHERE record_type = ? AND status = ?`, recordType, status).Scan(&count)
	return count, err
}

func PayloadMap(record Record) map[string]any {
	out := map[string]any{}
	_ = json.Unmarshal([]byte(record.PayloadJSON), &out)
	out["id"] = record.ID
	out["record_type"] = record.RecordType
	out["status"] = record.Status
	out["owner_id"] = record.OwnerID
	out["parent_id"] = record.ParentID
	out["created_at"] = record.CreatedAt
	out["updated_at"] = record.UpdatedAt
	return out
}

func scanRecord(scanner interface{ Scan(dest ...any) error }) (Record, error) {
	var record Record
	var createdAt sql.NullTime
	var updatedAt sql.NullTime
	if err := scanner.Scan(&record.ID, &record.RecordType, &record.Status, &record.OwnerID, &record.ParentID, &record.PayloadJSON, &createdAt, &updatedAt); err != nil {
		return Record{}, err
	}
	if createdAt.Valid {
		record.CreatedAt = createdAt.Time.Format(timeFormatMySQLCompatible)
	}
	if updatedAt.Valid {
		record.UpdatedAt = updatedAt.Time.Format(timeFormatMySQLCompatible)
	}
	return record, nil
}

func scanRecords(rows *sql.Rows) ([]Record, error) {
	var records []Record
	for rows.Next() {
		record, err := scanRecord(rows)
		if err != nil {
			return nil, err
		}
		records = append(records, record)
	}
	return records, rows.Err()
}

func recordPrefix(recordType string) string {
	switch recordType {
	case "enterprise_customer":
		return "cust"
	case "enterprise_contract":
		return "contract"
	case "enterprise_order":
		return "order"
	case "delivery_grant":
		return "grant"
	case "payout_batch":
		return "batch"
	case "payout_transfer":
		return "transfer"
	case "buyer_usage_report":
		return "usage"
	case "payout_profile":
		return "profile"
	case "inbox":
		return "inbox"
	case "inbound_message":
		return "inbound"
	case "webhook_case":
		return "webhook"
	case "review_sample":
		return "sample"
	case "dataset_eval":
		return "eval"
	case "reconciliation":
		return "recon"
	case "dsr":
		return "dsr"
	case "invoice":
		return "invoice"
	case "sso_provider":
		return "sso"
	case "provider_config":
		return "provider"
	case "compliance_task":
		return "compliance"
	case "content_safety":
		return "safety"
	default:
		return "rec"
	}
}

const timeFormatMySQLCompatible = "2006-01-02T15:04:05.999999Z07:00"
