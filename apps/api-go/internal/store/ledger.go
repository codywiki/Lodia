package store

import (
	"context"
	"database/sql"
	"fmt"
	"time"
)

type UsageEvent struct {
	ID                string
	EventType         string
	DatasetID         string
	OrderID           string
	BuyerID           string
	Status            string
	GrossRevenueCents int64
	DirectCostCents   int64
	NetRevenueCents   int64
	PayloadJSON       string
	CreatedAt         time.Time
	UpdatedAt         time.Time
}

type PayoutEvent struct {
	ID            string
	UsageEventID  string
	DatasetID     string
	CaseID        string
	ContributorID string
	BatchID       string
	Status        string
	AmountCents   int64
	Weight        float64
	PayloadJSON   string
	CreatedAt     time.Time
	UpdatedAt     time.Time
}

func (db *DB) CreateUsageEvent(ctx context.Context, event *UsageEvent) error {
	now := nowUTC()
	if event.ID == "" {
		event.ID = NewID("usage")
	}
	if event.EventType == "" {
		event.EventType = "dataset_exported"
	}
	if event.Status == "" {
		event.Status = "billable"
	}
	if event.PayloadJSON == "" {
		event.PayloadJSON = "{}"
	}
	event.CreatedAt = now
	event.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO usage_events
			(id, event_type, dataset_id, order_id, buyer_id, status, gross_revenue_cents, direct_cost_cents, net_revenue_cents, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, NULLIF(?, ''), NULLIF(?, ''), ?, ?, ?, ?, ?, ?, ?)
	`, event.ID, event.EventType, event.DatasetID, event.OrderID, event.BuyerID, event.Status, event.GrossRevenueCents, event.DirectCostCents, event.NetRevenueCents, event.PayloadJSON, event.CreatedAt, event.UpdatedAt)
	return err
}

func (db *DB) CreatePayoutEvent(ctx context.Context, event *PayoutEvent) error {
	now := nowUTC()
	if event.ID == "" {
		event.ID = NewID("payout")
	}
	if event.Status == "" {
		event.Status = "pending"
	}
	if event.PayloadJSON == "" {
		event.PayloadJSON = "{}"
	}
	event.CreatedAt = now
	event.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO payout_events
			(id, usage_event_id, dataset_id, case_id, contributor_id, batch_id, status, amount_cents, weight, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?, ?, ?, ?, ?)
	`, event.ID, event.UsageEventID, event.DatasetID, event.CaseID, event.ContributorID, event.BatchID, event.Status, event.AmountCents, event.Weight, event.PayloadJSON, event.CreatedAt, event.UpdatedAt)
	return err
}

func (db *DB) CreateUsageEventWithPayouts(ctx context.Context, usage *UsageEvent, payouts []PayoutEvent) error {
	now := nowUTC()
	if usage.ID == "" {
		usage.ID = NewID("usage")
	}
	if usage.EventType == "" {
		usage.EventType = "dataset_exported"
	}
	if usage.Status == "" {
		usage.Status = "billable"
	}
	if usage.PayloadJSON == "" {
		usage.PayloadJSON = "{}"
	}
	usage.CreatedAt = now
	usage.UpdatedAt = now
	tx, err := db.sql.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO usage_events
			(id, event_type, dataset_id, order_id, buyer_id, status, gross_revenue_cents, direct_cost_cents, net_revenue_cents, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, NULLIF(?, ''), NULLIF(?, ''), ?, ?, ?, ?, ?, ?, ?)
	`, usage.ID, usage.EventType, usage.DatasetID, usage.OrderID, usage.BuyerID, usage.Status, usage.GrossRevenueCents, usage.DirectCostCents, usage.NetRevenueCents, usage.PayloadJSON, usage.CreatedAt, usage.UpdatedAt); err != nil {
		_ = tx.Rollback()
		return err
	}
	for i := range payouts {
		if payouts[i].ID == "" {
			payouts[i].ID = NewID("payout")
		}
		if payouts[i].UsageEventID == "" {
			payouts[i].UsageEventID = usage.ID
		}
		if payouts[i].Status == "" {
			payouts[i].Status = "pending"
		}
		if payouts[i].PayloadJSON == "" {
			payouts[i].PayloadJSON = "{}"
		}
		payouts[i].CreatedAt = now
		payouts[i].UpdatedAt = now
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO payout_events
				(id, usage_event_id, dataset_id, case_id, contributor_id, batch_id, status, amount_cents, weight, payload_json, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?, ?, ?, ?, ?)
		`, payouts[i].ID, payouts[i].UsageEventID, payouts[i].DatasetID, payouts[i].CaseID, payouts[i].ContributorID, payouts[i].BatchID, payouts[i].Status, payouts[i].AmountCents, payouts[i].Weight, payouts[i].PayloadJSON, payouts[i].CreatedAt, payouts[i].UpdatedAt); err != nil {
			_ = tx.Rollback()
			return err
		}
	}
	return tx.Commit()
}

func (db *DB) ListPayoutEventsByStatus(ctx context.Context, status string, limit int) ([]PayoutEvent, error) {
	if limit <= 0 || limit > 1000 {
		limit = 100
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, usage_event_id, dataset_id, case_id, contributor_id, COALESCE(batch_id, ''), status, amount_cents, weight, CAST(payload_json AS CHAR), created_at, updated_at
		FROM payout_events WHERE status = ? ORDER BY created_at ASC LIMIT ?
	`, status, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	events := []PayoutEvent{}
	for rows.Next() {
		event, err := scanPayoutEvent(rows)
		if err != nil {
			return nil, err
		}
		events = append(events, event)
	}
	return events, rows.Err()
}

func (db *DB) ActivePayoutDisputeBlockers(ctx context.Context, events []PayoutEvent, limit int) ([]DisputeBlocker, error) {
	entities := map[string][]string{}
	for _, event := range events {
		entities["payout_event"] = append(entities["payout_event"], event.ID)
		entities["dataset"] = append(entities["dataset"], event.DatasetID)
		entities["case"] = append(entities["case"], event.CaseID)
		entities["contributor"] = append(entities["contributor"], event.ContributorID)
	}
	return db.ActiveDisputeBlockers(ctx, entities, true, limit)
}

func (db *DB) ActivePayoutBatchDisputeBlockers(ctx context.Context, batchID string, limit int) ([]DisputeBlocker, error) {
	if limit <= 0 || limit > 1000 {
		limit = 100
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT d.id, d.entity_type, d.entity_id, d.reason
		FROM payout_events pe
		JOIN disputes d ON d.status = 'open'
			AND d.held_payout_count > 0
			AND (
				(d.entity_type = 'payout_event' AND d.entity_id = pe.id)
				OR (d.entity_type = 'dataset' AND d.entity_id = pe.dataset_id)
				OR (d.entity_type = 'case' AND d.entity_id = pe.case_id)
				OR (d.entity_type = 'contributor' AND d.entity_id = pe.contributor_id)
			)
		WHERE pe.batch_id = ?
		GROUP BY d.id, d.entity_type, d.entity_id, d.reason
		ORDER BY MAX(d.created_at) DESC
		LIMIT ?
	`, batchID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	blockers := []DisputeBlocker{}
	for rows.Next() {
		var blocker DisputeBlocker
		if err := rows.Scan(&blocker.ID, &blocker.EntityType, &blocker.EntityID, &blocker.Reason); err != nil {
			return nil, err
		}
		blockers = append(blockers, blocker)
	}
	return blockers, rows.Err()
}

func (db *DB) AttachPayoutEventsToBatch(ctx context.Context, batchID string, eventIDs []string) error {
	tx, err := db.sql.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	for _, eventID := range eventIDs {
		result, err := tx.ExecContext(ctx, `
			UPDATE payout_events SET status = 'batched', batch_id = ?, updated_at = ? WHERE id = ? AND status = 'pending'
		`, batchID, nowUTC(), eventID)
		if err != nil {
			_ = tx.Rollback()
			return err
		}
		if rows, _ := result.RowsAffected(); rows != 1 {
			_ = tx.Rollback()
			return fmt.Errorf("payout_event_unavailable:%s", eventID)
		}
	}
	return tx.Commit()
}

func (db *DB) CreatePayoutBatchForEvents(ctx context.Context, batch *PayoutBatch, eventIDs []string) error {
	if len(eventIDs) == 0 {
		return fmt.Errorf("no_pending_payout_events")
	}
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
	tx, err := db.sql.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO payout_batches
			(id, status, payout_count, total_amount_cents, min_amount_cents, max_events, settled_at, payload_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, batch.ID, batch.Status, batch.PayoutCount, batch.TotalAmountCents, batch.MinAmountCents, batch.MaxEvents, batch.SettledAt, batch.PayloadJSON, batch.CreatedAt, batch.UpdatedAt); err != nil {
		_ = tx.Rollback()
		return err
	}
	for _, eventID := range eventIDs {
		result, err := tx.ExecContext(ctx, `
			UPDATE payout_events SET status = 'batched', batch_id = ?, updated_at = ? WHERE id = ? AND status = 'pending'
		`, batch.ID, now, eventID)
		if err != nil {
			_ = tx.Rollback()
			return err
		}
		if rows, _ := result.RowsAffected(); rows != 1 {
			_ = tx.Rollback()
			return fmt.Errorf("payout_event_unavailable:%s", eventID)
		}
	}
	return tx.Commit()
}

func (db *DB) SettlePayoutBatchAndEvents(ctx context.Context, id string) (PayoutBatch, error) {
	now := nowUTC()
	tx, err := db.sql.BeginTx(ctx, nil)
	if err != nil {
		return PayoutBatch{}, err
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE payout_batches SET status = 'settled', settled_at = COALESCE(settled_at, ?), updated_at = ? WHERE id = ? AND status <> 'settled'
	`, now, now, id); err != nil {
		_ = tx.Rollback()
		return PayoutBatch{}, err
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE payout_events SET status = 'settled', updated_at = ? WHERE batch_id = ? AND status <> 'settled'
	`, now, id); err != nil {
		_ = tx.Rollback()
		return PayoutBatch{}, err
	}
	if err := tx.Commit(); err != nil {
		return PayoutBatch{}, err
	}
	return db.GetPayoutBatch(ctx, id)
}

func (db *DB) SettlePayoutEventsByBatch(ctx context.Context, batchID string) error {
	_, err := db.sql.ExecContext(ctx, `
		UPDATE payout_events SET status = 'settled', updated_at = ? WHERE batch_id = ? AND status <> 'settled'
	`, nowUTC(), batchID)
	return err
}

func (db *DB) PayoutStatusCounts(ctx context.Context) (map[string]int64, error) {
	return db.countBy(ctx, "payout_events", "status")
}

func (db *DB) SumPayoutEvents(ctx context.Context, status string) (int64, error) {
	query := `SELECT COALESCE(SUM(amount_cents), 0) FROM payout_events`
	args := []any{}
	if status != "" {
		query += ` WHERE status = ?`
		args = append(args, status)
	}
	var sum int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&sum)
	return sum, err
}

func (db *DB) ContributorLedgerSummary(ctx context.Context, contributorID string) (map[string]any, error) {
	rows, err := db.sql.QueryContext(ctx, `
		SELECT status, COUNT(*), COALESCE(SUM(amount_cents), 0)
		FROM payout_events WHERE contributor_id = ? GROUP BY status
	`, contributorID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	counts := map[string]int64{"pending": 0, "batched": 0, "settled": 0}
	amounts := map[string]int64{"pending": 0, "batched": 0, "settled": 0}
	for rows.Next() {
		var status string
		var count int64
		var amount int64
		if err := rows.Scan(&status, &count, &amount); err != nil {
			return nil, err
		}
		counts[status] = count
		amounts[status] = amount
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	total := int64(0)
	totalCount := int64(0)
	for status, amount := range amounts {
		total += amount
		totalCount += counts[status]
	}
	return map[string]any{
		"pending_cents": amounts["pending"],
		"batched_cents": amounts["batched"],
		"settled_cents": amounts["settled"],
		"total_cents":   total,
		"payout_count":  totalCount,
		"by_status":     counts,
	}, nil
}

func UsageEventPayload(event UsageEvent) map[string]any {
	return map[string]any{
		"id":                  event.ID,
		"event_type":          event.EventType,
		"dataset_id":          event.DatasetID,
		"order_id":            event.OrderID,
		"buyer_id":            event.BuyerID,
		"status":              event.Status,
		"gross_revenue_cents": event.GrossRevenueCents,
		"direct_cost_cents":   event.DirectCostCents,
		"net_revenue_cents":   event.NetRevenueCents,
		"payload":             jsonMap(event.PayloadJSON),
		"created_at":          event.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":          event.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func PayoutEventPayload(event PayoutEvent) map[string]any {
	return map[string]any{
		"id":             event.ID,
		"usage_event_id": event.UsageEventID,
		"dataset_id":     event.DatasetID,
		"case_id":        event.CaseID,
		"contributor_id": event.ContributorID,
		"batch_id":       event.BatchID,
		"status":         event.Status,
		"amount_cents":   event.AmountCents,
		"weight":         event.Weight,
		"payload":        jsonMap(event.PayloadJSON),
		"created_at":     event.CreatedAt.UTC().Format(timeFormatMySQLCompatible),
		"updated_at":     event.UpdatedAt.UTC().Format(timeFormatMySQLCompatible),
	}
}

func scanPayoutEvent(scanner interface{ Scan(dest ...any) error }) (PayoutEvent, error) {
	var event PayoutEvent
	err := scanner.Scan(&event.ID, &event.UsageEventID, &event.DatasetID, &event.CaseID, &event.ContributorID, &event.BatchID, &event.Status, &event.AmountCents, &event.Weight, &event.PayloadJSON, &event.CreatedAt, &event.UpdatedAt)
	return event, err
}

func scanUsageEvent(scanner interface{ Scan(dest ...any) error }) (UsageEvent, error) {
	var event UsageEvent
	var orderID sql.NullString
	var buyerID sql.NullString
	err := scanner.Scan(&event.ID, &event.EventType, &event.DatasetID, &orderID, &buyerID, &event.Status, &event.GrossRevenueCents, &event.DirectCostCents, &event.NetRevenueCents, &event.PayloadJSON, &event.CreatedAt, &event.UpdatedAt)
	event.OrderID = orderID.String
	event.BuyerID = buyerID.String
	return event, err
}

var typedLedgerSchemaStatements = []string{
	`CREATE TABLE IF NOT EXISTS usage_events (
		id VARCHAR(64) PRIMARY KEY,
		event_type VARCHAR(64) NOT NULL,
		dataset_id VARCHAR(64) NOT NULL,
		order_id VARCHAR(64) NULL,
		buyer_id VARCHAR(128) NULL,
		status VARCHAR(32) NOT NULL,
		gross_revenue_cents BIGINT NOT NULL DEFAULT 0,
		direct_cost_cents BIGINT NOT NULL DEFAULT 0,
		net_revenue_cents BIGINT NOT NULL DEFAULT 0,
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_usage_events_dataset_created (dataset_id, created_at),
		KEY idx_usage_events_type_status (event_type, status),
		KEY idx_usage_events_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS payout_events (
		id VARCHAR(64) PRIMARY KEY,
		usage_event_id VARCHAR(64) NOT NULL,
		dataset_id VARCHAR(64) NOT NULL,
		case_id VARCHAR(64) NOT NULL,
		contributor_id VARCHAR(128) NOT NULL,
		batch_id VARCHAR(64) NULL,
		status VARCHAR(32) NOT NULL,
		amount_cents BIGINT NOT NULL DEFAULT 0,
		weight DOUBLE NOT NULL DEFAULT 0,
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		UNIQUE KEY uq_payout_events_usage_case (usage_event_id, case_id),
		KEY idx_payout_events_status_created (status, created_at),
		KEY idx_payout_events_contributor_status (contributor_id, status),
		KEY idx_payout_events_batch_status (batch_id, status),
		KEY idx_payout_events_dataset_created (dataset_id, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
}
