package store

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
)

type MigrationStatus struct {
	OK                 bool             `json:"ok"`
	LatestExpected     string           `json:"latest_expected"`
	LatestApplied      string           `json:"latest_applied"`
	ExpectedCount      int              `json:"expected_count"`
	AppliedCount       int              `json:"applied_count"`
	MissingVersions    []string         `json:"missing_versions"`
	ChecksumMismatches []string         `json:"checksum_mismatches"`
	Applied            []MigrationEntry `json:"applied"`
}

type MigrationEntry struct {
	ID          string `json:"id"`
	Checksum    string `json:"checksum"`
	Description string `json:"description"`
	Status      string `json:"status"`
	AppliedAt   string `json:"applied_at"`
}

func (db *DB) RegisterExpectedMigrations(ctx context.Context) error {
	now := nowUTC()
	for _, migration := range expectedMigrations() {
		_, err := db.sql.ExecContext(ctx, `
			INSERT INTO schema_migrations (id, checksum, description, status, applied_at, created_at)
			VALUES (?, ?, ?, 'applied', ?, ?)
			ON DUPLICATE KEY UPDATE id = id
		`, migration.ID, migration.Checksum, migration.Description, now, now)
		if err != nil {
			return err
		}
	}
	return nil
}

func (db *DB) MigrationStatus(ctx context.Context) (MigrationStatus, error) {
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, checksum, description, status, applied_at
		FROM schema_migrations ORDER BY applied_at ASC
	`)
	if err != nil {
		return MigrationStatus{}, err
	}
	defer rows.Close()
	appliedByID := map[string]MigrationEntry{}
	applied := []MigrationEntry{}
	for rows.Next() {
		var entry MigrationEntry
		var appliedAt sql.NullTime
		if err := rows.Scan(&entry.ID, &entry.Checksum, &entry.Description, &entry.Status, &appliedAt); err != nil {
			return MigrationStatus{}, err
		}
		if appliedAt.Valid {
			entry.AppliedAt = appliedAt.Time.UTC().Format(timeFormatMySQLCompatible)
		}
		appliedByID[entry.ID] = entry
		applied = append(applied, entry)
	}
	if err := rows.Err(); err != nil {
		return MigrationStatus{}, err
	}
	expected := expectedMigrations()
	status := MigrationStatus{
		OK:                 true,
		LatestExpected:     expected[len(expected)-1].ID,
		ExpectedCount:      len(expected),
		AppliedCount:       len(applied),
		MissingVersions:    []string{},
		ChecksumMismatches: []string{},
		Applied:            applied,
	}
	if len(applied) > 0 {
		status.LatestApplied = applied[len(applied)-1].ID
	}
	for _, migration := range expected {
		entry, ok := appliedByID[migration.ID]
		if !ok {
			status.MissingVersions = append(status.MissingVersions, migration.ID)
			status.OK = false
			continue
		}
		if entry.Checksum != migration.Checksum || entry.Status != "applied" {
			status.ChecksumMismatches = append(status.ChecksumMismatches, migration.ID)
			status.OK = false
		}
	}
	return status, nil
}

func (db *DB) MigrationPlan(ctx context.Context) (map[string]any, error) {
	status, err := db.MigrationStatus(ctx)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"target_version":    status.LatestExpected,
		"current_version":   status.LatestApplied,
		"pending_versions":  status.MissingVersions,
		"rollback_versions": []string{},
		"checksum_warnings": status.ChecksumMismatches,
		"ok":                status.OK,
	}, nil
}

func expectedMigrations() []MigrationEntry {
	return []MigrationEntry{
		{
			ID:          "20260508_001_go_mysql_core_schema",
			Checksum:    checksumStrings(schemaStatements),
			Description: "Go MySQL core schema, records control plane, users, auth tokens, and migration registry",
			Status:      "applied",
		},
		{
			ID:          "20260508_002_typed_business_tables",
			Checksum:    checksumStrings(typedBusinessSchemaStatements),
			Description: "Typed payout, enterprise, delivery, DSR, provider, and compliance tables",
			Status:      "applied",
		},
		{
			ID:          "20260508_003_typed_operational_tables",
			Checksum:    checksumStrings(typedOperationalSchemaStatements),
			Description: "Typed inbox, review, invoice, SSO, content safety, dispute, and payout profile tables",
			Status:      "applied",
		},
		{
			ID:          "20260509_004_typed_ledger_tables",
			Checksum:    checksumStrings(typedLedgerSchemaStatements),
			Description: "Typed usage and payout event ledger tables",
			Status:      "applied",
		},
		{
			ID:          "20260510_005_model_gateway_audit_tables",
			Checksum:    checksumStrings(typedModelGatewaySchemaStatements),
			Description: "Domestic model gateway and vendor processing audit tables",
			Status:      "applied",
		},
	}
}

func checksumStrings(values []string) string {
	h := sha256.New()
	for _, value := range values {
		_, _ = h.Write([]byte(value))
		_, _ = h.Write([]byte{0})
	}
	return hex.EncodeToString(h.Sum(nil))
}
