package store

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

var ErrNotFound = sql.ErrNoRows

type DB struct {
	sql *sql.DB
}

type Submission struct {
	ID                string
	OwnerID           string
	SourceType        string
	Status            string
	RawObjectURI      string
	RawHash           string
	DuplicateOfCaseID string
	AllowedUses       []string
	RawExpiresAt      *time.Time
	RawDeletedAt      *time.Time
	CreatedAt         time.Time
	UpdatedAt         time.Time
}

type Case struct {
	ID              string
	SubmissionID    string
	OwnerID         string
	Status          string
	RedactedText    string
	RawHash         string
	CanonicalHash   string
	DRL             string
	CommercialReady bool
	RedactionJSON   string
	AnnotationJSON  string
	QualityGateJSON string
	LongHorizonJSON string
	ReviewClaimedBy string
	ReviewClaimedAt *time.Time
	CreatedAt       time.Time
	UpdatedAt       time.Time
}

type Job struct {
	ID           string
	SubmissionID string
	QueueName    string
	JobType      string
	Status       string
	PayloadJSON  string
	Attempts     int
	MaxAttempts  int
	Error        string
	AvailableAt  *time.Time
	LockedAt     *time.Time
	LockedBy     string
	CreatedAt    time.Time
	UpdatedAt    time.Time
}

type Asset struct {
	ID                      string
	OwnerID                 string
	SubmissionID            string
	AuthorizationSnapshotID string
	Filename                string
	MediaType               string
	AssetType               string
	ByteSize                int64
	ObjectURI               string
	Status                  string
	CreatedAt               time.Time
	UpdatedAt               time.Time
}

type Dataset struct {
	ID        string
	Name      string
	Status    string
	Purpose   string
	MinDRL    string
	CaseIDs   []string
	CreatedAt time.Time
	UpdatedAt time.Time
}

type DatasetArtifact struct {
	ID           string
	DatasetID    string
	ArtifactType string
	ObjectURI    string
	ContentType  string
	ByteSize     int64
	CreatedAt    time.Time
}

type AuditLog struct {
	ID          string
	ActorID     string
	EventType   string
	EntityType  string
	EntityID    string
	PayloadJSON string
	CreatedAt   time.Time
}

func Open(ctx context.Context, dsn string) (*DB, error) {
	handle, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, err
	}
	handle.SetMaxOpenConns(80)
	handle.SetMaxIdleConns(20)
	handle.SetConnMaxLifetime(30 * time.Minute)
	handle.SetConnMaxIdleTime(5 * time.Minute)
	if err := handle.PingContext(ctx); err != nil {
		_ = handle.Close()
		return nil, err
	}
	db := &DB{sql: handle}
	if err := db.Migrate(ctx); err != nil {
		_ = handle.Close()
		return nil, err
	}
	return db, nil
}

func (db *DB) Close() error {
	if db == nil || db.sql == nil {
		return nil
	}
	return db.sql.Close()
}

func (db *DB) Health(ctx context.Context) map[string]any {
	err := db.sql.PingContext(ctx)
	return map[string]any{"ok": err == nil, "backend": "mysql"}
}

func (db *DB) Migrate(ctx context.Context) error {
	for _, stmt := range schemaStatements {
		if _, err := db.sql.ExecContext(ctx, stmt); err != nil {
			return err
		}
	}
	for _, stmt := range typedBusinessSchemaStatements {
		if _, err := db.sql.ExecContext(ctx, stmt); err != nil {
			return err
		}
	}
	for _, stmt := range typedOperationalSchemaStatements {
		if _, err := db.sql.ExecContext(ctx, stmt); err != nil {
			return err
		}
	}
	for _, stmt := range typedLedgerSchemaStatements {
		if _, err := db.sql.ExecContext(ctx, stmt); err != nil {
			return err
		}
	}
	for _, stmt := range typedModelGatewaySchemaStatements {
		if _, err := db.sql.ExecContext(ctx, stmt); err != nil {
			return err
		}
	}
	return db.RegisterExpectedMigrations(ctx)
}

func (db *DB) CreateSubmission(ctx context.Context, sub *Submission) error {
	now := nowUTC()
	if sub.ID == "" {
		sub.ID = NewID("sub")
	}
	if sub.Status == "" {
		sub.Status = "queued"
	}
	sub.CreatedAt = now
	sub.UpdatedAt = now
	allowed, err := jsonText(sub.AllowedUses)
	if err != nil {
		return err
	}
	_, err = db.sql.ExecContext(ctx, `
		INSERT INTO submissions
			(id, owner_id, source_type, status, raw_object_uri, raw_hash, duplicate_of_case_id, allowed_uses_json, raw_expires_at, raw_deleted_at, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?, ?, ?, ?)
	`, sub.ID, sub.OwnerID, sub.SourceType, sub.Status, sub.RawObjectURI, sub.RawHash, sub.DuplicateOfCaseID, allowed, sub.RawExpiresAt, sub.RawDeletedAt, sub.CreatedAt, sub.UpdatedAt)
	return err
}

func (db *DB) GetSubmission(ctx context.Context, id string) (Submission, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, owner_id, source_type, status, raw_object_uri, raw_hash, COALESCE(duplicate_of_case_id, ''),
			CAST(allowed_uses_json AS CHAR), raw_expires_at, raw_deleted_at, created_at, updated_at
		FROM submissions WHERE id = ?
	`, id)
	return scanSubmission(row)
}

func (db *DB) UpdateSubmissionStatus(ctx context.Context, id string, status string, duplicateOfCaseID string) error {
	_, err := db.sql.ExecContext(ctx, `
		UPDATE submissions
		SET status = ?, duplicate_of_case_id = NULLIF(?, ''), updated_at = ?
		WHERE id = ?
	`, status, duplicateOfCaseID, nowUTC(), id)
	return err
}

func (db *DB) MarkSubmissionRawDeleted(ctx context.Context, id string) error {
	_, err := db.sql.ExecContext(ctx, `
		UPDATE submissions SET raw_deleted_at = ?, updated_at = ? WHERE id = ?
	`, nowUTC(), nowUTC(), id)
	return err
}

func (db *DB) CreateCase(ctx context.Context, c *Case) error {
	now := nowUTC()
	if c.ID == "" {
		c.ID = NewID("case")
	}
	if c.Status == "" {
		c.Status = "review_ready"
	}
	c.CreatedAt = now
	c.UpdatedAt = now
	res, err := db.sql.ExecContext(ctx, `
		INSERT IGNORE INTO cases
			(id, submission_id, owner_id, status, redacted_text, raw_hash, canonical_hash, drl, commercial_ready,
			 redaction_json, annotation_json, quality_gate_json, long_horizon_json, review_claimed_by, review_claimed_at, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''), NULLIF(?, ''), ?, ?, ?)
	`, c.ID, c.SubmissionID, c.OwnerID, c.Status, c.RedactedText, c.RawHash, c.CanonicalHash, c.DRL, c.CommercialReady, c.RedactionJSON, c.AnnotationJSON, c.QualityGateJSON, c.LongHorizonJSON, c.ReviewClaimedBy, c.ReviewClaimedAt, c.CreatedAt, c.UpdatedAt)
	if err != nil {
		return err
	}
	rows, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if rows == 0 {
		return errors.New("duplicate_case")
	}
	return nil
}

func (db *DB) GetCase(ctx context.Context, id string) (Case, error) {
	row := db.sql.QueryRowContext(ctx, caseSelectSQL("WHERE id = ?"), id)
	return scanCase(row)
}

func (db *DB) GetCaseBySubmission(ctx context.Context, submissionID string) (Case, error) {
	row := db.sql.QueryRowContext(ctx, caseSelectSQL("WHERE submission_id = ?"), submissionID)
	return scanCase(row)
}

func (db *DB) FindCaseByCanonicalHash(ctx context.Context, canonicalHash string) (Case, error) {
	row := db.sql.QueryRowContext(ctx, caseSelectSQL("WHERE canonical_hash = ?"), canonicalHash)
	return scanCase(row)
}

func (db *DB) ListCases(ctx context.Context, limit int) ([]Case, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, caseSelectSQL("ORDER BY created_at DESC LIMIT ?"), limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanCases(rows)
}

func (db *DB) ListCasesByOwner(ctx context.Context, ownerID string, limit int) ([]Case, error) {
	if limit <= 0 || limit > 500 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, caseSelectSQL("WHERE owner_id = ? ORDER BY created_at DESC LIMIT ?"), ownerID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanCases(rows)
}

func (db *DB) CaseStatusCountsByOwner(ctx context.Context, ownerID string) (map[string]int64, error) {
	return db.countByOwner(ctx, "cases", "status", ownerID)
}

func (db *DB) CaseDRLCountsByOwner(ctx context.Context, ownerID string) (map[string]int64, error) {
	return db.countByOwner(ctx, "cases", "drl", ownerID)
}

func (db *DB) CountCommercialReadyCasesByOwner(ctx context.Context, ownerID string) (int64, error) {
	var count int64
	err := db.sql.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM cases WHERE owner_id = ? AND commercial_ready = 1
	`, ownerID).Scan(&count)
	return count, err
}

func (db *DB) ListReviewQueue(ctx context.Context, limit int) ([]Case, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, caseSelectSQL("WHERE status IN ('review_ready', 'needs_review', 'in_review') ORDER BY created_at DESC LIMIT ?"), limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanCases(rows)
}

func (db *DB) ClaimNextCase(ctx context.Context, reviewerID string) (Case, error) {
	tx, err := db.sql.BeginTx(ctx, nil)
	if err != nil {
		return Case{}, err
	}
	defer func() { _ = tx.Rollback() }()
	var id string
	err = tx.QueryRowContext(ctx, `
		SELECT id
		FROM cases
		WHERE status IN ('review_ready', 'needs_review', 'in_review')
			AND (review_claimed_by IS NULL OR review_claimed_at < DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 30 MINUTE))
		ORDER BY created_at
		LIMIT 1
		FOR UPDATE
	`).Scan(&id)
	if err != nil {
		return Case{}, err
	}
	now := nowUTC()
	if _, err = tx.ExecContext(ctx, `
		UPDATE cases SET status = 'in_review', review_claimed_by = ?, review_claimed_at = ?, updated_at = ? WHERE id = ?
	`, reviewerID, now, now, id); err != nil {
		return Case{}, err
	}
	if err = tx.Commit(); err != nil {
		return Case{}, err
	}
	return db.GetCase(ctx, id)
}

func (db *DB) ReleaseCase(ctx context.Context, caseID string) error {
	_, err := db.sql.ExecContext(ctx, `
		UPDATE cases SET status = 'review_ready', review_claimed_by = NULL, review_claimed_at = NULL, updated_at = ? WHERE id = ?
	`, nowUTC(), caseID)
	return err
}

func (db *DB) SetCaseStatus(ctx context.Context, caseID string, status string) error {
	_, err := db.sql.ExecContext(ctx, `UPDATE cases SET status = ?, updated_at = ? WHERE id = ?`, status, nowUTC(), caseID)
	return err
}

func (db *DB) UpdateLongHorizon(ctx context.Context, caseID string, annotationJSON string, qualityGateJSON string, longHorizonJSON string, drl string, commercialReady bool, status string) error {
	_, err := db.sql.ExecContext(ctx, `
		UPDATE cases
		SET annotation_json = ?,
			quality_gate_json = ?,
			long_horizon_json = ?,
			drl = ?,
			commercial_ready = ?,
			status = ?,
			updated_at = ?
		WHERE id = ?
	`, annotationJSON, qualityGateJSON, longHorizonJSON, drl, commercialReady, status, nowUTC(), caseID)
	return err
}

func (db *DB) CreateJob(ctx context.Context, job *Job) error {
	now := nowUTC()
	if job.ID == "" {
		job.ID = NewID("job")
	}
	if job.Status == "" {
		job.Status = "queued"
	}
	if job.MaxAttempts == 0 {
		job.MaxAttempts = 5
	}
	job.CreatedAt = now
	job.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO jobs
			(id, submission_id, queue_name, job_type, status, payload_json, attempts, max_attempts, error, available_at, locked_at, locked_by, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?)
	`, job.ID, job.SubmissionID, job.QueueName, job.JobType, job.Status, job.PayloadJSON, job.Attempts, job.MaxAttempts, job.Error, job.AvailableAt, job.LockedAt, job.LockedBy, job.CreatedAt, job.UpdatedAt)
	return err
}

func (db *DB) GetJob(ctx context.Context, id string) (Job, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, submission_id, queue_name, job_type, status, CAST(payload_json AS CHAR), attempts, max_attempts,
			COALESCE(error, ''), available_at, locked_at, COALESCE(locked_by, ''), created_at, updated_at
		FROM jobs WHERE id = ?
	`, id)
	return scanJob(row)
}

func (db *DB) JobsBySubmission(ctx context.Context, submissionID string) ([]Job, error) {
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, submission_id, queue_name, job_type, status, CAST(payload_json AS CHAR), attempts, max_attempts,
			COALESCE(error, ''), available_at, locked_at, COALESCE(locked_by, ''), created_at, updated_at
		FROM jobs WHERE submission_id = ? ORDER BY created_at DESC
	`, submissionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var jobs []Job
	for rows.Next() {
		job, err := scanJob(rows)
		if err != nil {
			return nil, err
		}
		jobs = append(jobs, job)
	}
	return jobs, rows.Err()
}

func (db *DB) MarkJobRunning(ctx context.Context, id string, workerID string) (Job, bool, error) {
	now := nowUTC()
	res, err := db.sql.ExecContext(ctx, `
		UPDATE jobs
		SET status = 'running', attempts = attempts + 1, locked_at = ?, locked_by = ?, updated_at = ?
		WHERE id = ? AND status IN ('queued', 'retry')
	`, now, workerID, now, id)
	if err != nil {
		return Job{}, false, err
	}
	rows, err := res.RowsAffected()
	if err != nil {
		return Job{}, false, err
	}
	job, err := db.GetJob(ctx, id)
	if err != nil {
		return Job{}, false, err
	}
	return job, rows > 0, nil
}

func (db *DB) MarkJobDone(ctx context.Context, id string) error {
	_, err := db.sql.ExecContext(ctx, `
		UPDATE jobs SET status = 'done', error = '', locked_at = NULL, locked_by = NULL, updated_at = ? WHERE id = ?
	`, nowUTC(), id)
	return err
}

func (db *DB) MarkJobFailed(ctx context.Context, id string, message string) (bool, error) {
	job, err := db.GetJob(ctx, id)
	if err != nil {
		return false, err
	}
	nextStatus := "failed"
	shouldRetry := false
	if job.Attempts < job.MaxAttempts {
		nextStatus = "retry"
		shouldRetry = true
	}
	_, err = db.sql.ExecContext(ctx, `
		UPDATE jobs SET status = ?, error = ?, locked_at = NULL, locked_by = NULL, updated_at = ? WHERE id = ?
	`, nextStatus, truncate(message, 1000), nowUTC(), id)
	return shouldRetry, err
}

func (db *DB) CreateAsset(ctx context.Context, asset *Asset) error {
	now := nowUTC()
	if asset.ID == "" {
		asset.ID = NewID("asset")
	}
	if asset.Status == "" {
		asset.Status = "stored"
	}
	asset.CreatedAt = now
	asset.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO assets
			(id, owner_id, submission_id, authorization_snapshot_id, filename, media_type, asset_type, byte_size, object_uri, status, created_at, updated_at)
		VALUES (?, ?, NULLIF(?, ''), NULLIF(?, ''), ?, ?, ?, ?, ?, ?, ?, ?)
	`, asset.ID, asset.OwnerID, asset.SubmissionID, asset.AuthorizationSnapshotID, asset.Filename, asset.MediaType, asset.AssetType, asset.ByteSize, asset.ObjectURI, asset.Status, asset.CreatedAt, asset.UpdatedAt)
	return err
}

func (db *DB) GetAsset(ctx context.Context, id string) (Asset, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, owner_id, COALESCE(submission_id, ''), COALESCE(authorization_snapshot_id, ''),
			filename, media_type, asset_type, byte_size, object_uri, status, created_at, updated_at
		FROM assets WHERE id = ?
	`, id)
	var asset Asset
	err := row.Scan(&asset.ID, &asset.OwnerID, &asset.SubmissionID, &asset.AuthorizationSnapshotID, &asset.Filename, &asset.MediaType, &asset.AssetType, &asset.ByteSize, &asset.ObjectURI, &asset.Status, &asset.CreatedAt, &asset.UpdatedAt)
	return asset, err
}

func (db *DB) ListAssets(ctx context.Context, limit int) ([]Asset, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, owner_id, COALESCE(submission_id, ''), COALESCE(authorization_snapshot_id, ''),
			filename, media_type, asset_type, byte_size, object_uri, status, created_at, updated_at
		FROM assets ORDER BY created_at DESC LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var assets []Asset
	for rows.Next() {
		var asset Asset
		if err := rows.Scan(&asset.ID, &asset.OwnerID, &asset.SubmissionID, &asset.AuthorizationSnapshotID, &asset.Filename, &asset.MediaType, &asset.AssetType, &asset.ByteSize, &asset.ObjectURI, &asset.Status, &asset.CreatedAt, &asset.UpdatedAt); err != nil {
			return nil, err
		}
		assets = append(assets, asset)
	}
	return assets, rows.Err()
}

func (db *DB) AssetStatusCountsByOwner(ctx context.Context, ownerID string) (map[string]int64, error) {
	return db.countByOwner(ctx, "assets", "status", ownerID)
}

func (db *DB) CountAssetsByOwner(ctx context.Context, ownerID string, status string) (int64, error) {
	query := `SELECT COUNT(*) FROM assets WHERE owner_id = ?`
	args := []any{ownerID}
	if status != "" {
		query += ` AND status = ?`
		args = append(args, status)
	}
	var count int64
	err := db.sql.QueryRowContext(ctx, query, args...).Scan(&count)
	return count, err
}

func (db *DB) UpdateAssetExtraction(ctx context.Context, assetID string, status string, submissionID string) error {
	_, err := db.sql.ExecContext(ctx, `
		UPDATE assets SET status = ?, submission_id = NULLIF(?, ''), updated_at = ? WHERE id = ?
	`, status, submissionID, nowUTC(), assetID)
	return err
}

func (db *DB) EligibleCases(ctx context.Context, limit int) ([]Case, error) {
	if limit <= 0 || limit > 5000 {
		limit = 100
	}
	rows, err := db.sql.QueryContext(ctx, caseSelectSQL("WHERE commercial_ready = 1 AND status IN ('approved', 'review_ready', 'in_review') ORDER BY updated_at DESC LIMIT ?"), limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanCases(rows)
}

func (db *DB) CreateDataset(ctx context.Context, dataset *Dataset) error {
	now := nowUTC()
	if dataset.ID == "" {
		dataset.ID = NewID("dataset")
	}
	if dataset.Status == "" {
		dataset.Status = "ready"
	}
	dataset.CreatedAt = now
	dataset.UpdatedAt = now
	caseIDs, err := jsonText(dataset.CaseIDs)
	if err != nil {
		return err
	}
	_, err = db.sql.ExecContext(ctx, `
		INSERT INTO datasets (id, name, status, purpose, min_drl, case_ids_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, dataset.ID, dataset.Name, dataset.Status, dataset.Purpose, dataset.MinDRL, caseIDs, dataset.CreatedAt, dataset.UpdatedAt)
	return err
}

func (db *DB) GetDataset(ctx context.Context, id string) (Dataset, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, name, status, purpose, min_drl, CAST(case_ids_json AS CHAR), created_at, updated_at
		FROM datasets WHERE id = ?
	`, id)
	return scanDataset(row)
}

func (db *DB) ListDatasets(ctx context.Context, limit int) ([]Dataset, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, name, status, purpose, min_drl, CAST(case_ids_json AS CHAR), created_at, updated_at
		FROM datasets ORDER BY created_at DESC LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var datasets []Dataset
	for rows.Next() {
		dataset, err := scanDataset(rows)
		if err != nil {
			return nil, err
		}
		datasets = append(datasets, dataset)
	}
	return datasets, rows.Err()
}

func (db *DB) CreateDatasetArtifact(ctx context.Context, artifact *DatasetArtifact) error {
	now := nowUTC()
	if artifact.ID == "" {
		artifact.ID = NewID("artifact")
	}
	artifact.CreatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO dataset_artifacts (id, dataset_id, artifact_type, object_uri, content_type, byte_size, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
		ON DUPLICATE KEY UPDATE object_uri = VALUES(object_uri), content_type = VALUES(content_type), byte_size = VALUES(byte_size), created_at = VALUES(created_at)
	`, artifact.ID, artifact.DatasetID, artifact.ArtifactType, artifact.ObjectURI, artifact.ContentType, artifact.ByteSize, artifact.CreatedAt)
	return err
}

func (db *DB) GetDatasetArtifact(ctx context.Context, datasetID string, artifactType string) (DatasetArtifact, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, dataset_id, artifact_type, object_uri, content_type, byte_size, created_at
		FROM dataset_artifacts WHERE dataset_id = ? AND artifact_type = ?
	`, datasetID, artifactType)
	return scanDatasetArtifact(row)
}

func (db *DB) ListDatasetArtifacts(ctx context.Context, datasetID string) ([]DatasetArtifact, error) {
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, dataset_id, artifact_type, object_uri, content_type, byte_size, created_at
		FROM dataset_artifacts WHERE dataset_id = ? ORDER BY artifact_type ASC
	`, datasetID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	artifacts := []DatasetArtifact{}
	for rows.Next() {
		artifact, err := scanDatasetArtifact(rows)
		if err != nil {
			return nil, err
		}
		artifacts = append(artifacts, artifact)
	}
	return artifacts, rows.Err()
}

func (db *DB) CreateReview(ctx context.Context, caseID string, reviewerID string, reviewType string, decision string, score float64, notes string, evidence any) error {
	evidenceJSON, err := jsonText(evidence)
	if err != nil {
		return err
	}
	_, err = db.sql.ExecContext(ctx, `
		INSERT INTO reviews (id, case_id, reviewer_id, review_type, decision, score, notes, evidence_json, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, NewID("review"), caseID, reviewerID, reviewType, decision, score, notes, evidenceJSON, nowUTC())
	return err
}

func (db *DB) Audit(ctx context.Context, actorID string, eventType string, entityType string, entityID string, payload any) error {
	payloadJSON, err := jsonText(payload)
	if err != nil {
		return err
	}
	_, err = db.sql.ExecContext(ctx, `
		INSERT INTO audit_logs (id, actor_id, event_type, entity_type, entity_id, payload_json, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, NewID("audit"), actorID, eventType, entityType, entityID, payloadJSON, nowUTC())
	return err
}

func (db *DB) ListAudit(ctx context.Context, limit int) ([]AuditLog, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, actor_id, event_type, entity_type, entity_id, CAST(payload_json AS CHAR), created_at
		FROM audit_logs ORDER BY created_at DESC LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var logs []AuditLog
	for rows.Next() {
		var log AuditLog
		if err := rows.Scan(&log.ID, &log.ActorID, &log.EventType, &log.EntityType, &log.EntityID, &log.PayloadJSON, &log.CreatedAt); err != nil {
			return nil, err
		}
		logs = append(logs, log)
	}
	return logs, rows.Err()
}

func (db *DB) Metrics(ctx context.Context) (map[string]any, error) {
	casesByStatus, err := db.countBy(ctx, "cases", "status")
	if err != nil {
		return nil, err
	}
	caseDRL, err := db.countBy(ctx, "cases", "drl")
	if err != nil {
		return nil, err
	}
	jobsByStatus, err := db.countBy(ctx, "jobs", "status")
	if err != nil {
		return nil, err
	}
	assetsByStatus, err := db.countBy(ctx, "assets", "status")
	if err != nil {
		return nil, err
	}
	reviewsByDecision, err := db.countBy(ctx, "reviews", "decision")
	if err != nil {
		return nil, err
	}
	datasets, _ := db.scalarCount(ctx, "datasets")
	auditEvents, _ := db.scalarCount(ctx, "audit_logs")
	contributors, _ := db.CountUsers(ctx, "contributor", "active")
	reviewers, _ := db.CountUsers(ctx, "reviewer", "active")
	admins, _ := db.CountUsers(ctx, "admin", "active")
	payouts, _ := db.PayoutStatusCounts(ctx)
	pendingPayoutCents, _ := db.SumPayoutEvents(ctx, "pending")
	payoutBatchCount, _ := db.scalarCount(ctx, "payout_batches")
	return map[string]any{
		"cases":                casesByStatus,
		"case_drl":             caseDRL,
		"jobs":                 jobsByStatus,
		"assets":               assetsByStatus,
		"reviews":              reviewsByDecision,
		"users":                map[string]int64{"contributors": contributors, "reviewers": reviewers, "admins": admins},
		"authorizations":       map[string]int64{"active": 1},
		"datasets":             datasets,
		"payouts":              payouts,
		"payout_batches":       map[string]int64{"created": payoutBatchCount},
		"pending_payout_cents": pendingPayoutCents,
		"audit_events":         auditEvents,
	}, nil
}

func (db *DB) countBy(ctx context.Context, table string, column string) (map[string]int64, error) {
	rows, err := db.sql.QueryContext(ctx, fmt.Sprintf("SELECT %s, COUNT(*) FROM %s GROUP BY %s", column, table, column))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]int64{}
	for rows.Next() {
		var key string
		var count int64
		if err := rows.Scan(&key, &count); err != nil {
			return nil, err
		}
		out[key] = count
	}
	return out, rows.Err()
}

func (db *DB) countByOwner(ctx context.Context, table string, column string, ownerID string) (map[string]int64, error) {
	rows, err := db.sql.QueryContext(ctx, fmt.Sprintf("SELECT %s, COUNT(*) FROM %s WHERE owner_id = ? GROUP BY %s", column, table, column), ownerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]int64{}
	for rows.Next() {
		var key string
		var count int64
		if err := rows.Scan(&key, &count); err != nil {
			return nil, err
		}
		out[key] = count
	}
	return out, rows.Err()
}

func (db *DB) scalarCount(ctx context.Context, table string) (int64, error) {
	var count int64
	err := db.sql.QueryRowContext(ctx, fmt.Sprintf("SELECT COUNT(*) FROM %s", table)).Scan(&count)
	return count, err
}

func NewID(prefix string) string {
	var bytes [12]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		return fmt.Sprintf("%s_%d", prefix, time.Now().UnixNano())
	}
	return prefix + "_" + hex.EncodeToString(bytes[:])
}

func jsonText(value any) (string, error) {
	if value == nil {
		return "{}", nil
	}
	out, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	return string(out), nil
}

func stringSlice(raw string) []string {
	var values []string
	if err := json.Unmarshal([]byte(raw), &values); err != nil {
		return []string{}
	}
	return values
}

func scanSubmission(scanner interface{ Scan(dest ...any) error }) (Submission, error) {
	var sub Submission
	var allowed string
	var duplicate sql.NullString
	var rawExpiresAt sql.NullTime
	var rawDeletedAt sql.NullTime
	err := scanner.Scan(&sub.ID, &sub.OwnerID, &sub.SourceType, &sub.Status, &sub.RawObjectURI, &sub.RawHash, &duplicate, &allowed, &rawExpiresAt, &rawDeletedAt, &sub.CreatedAt, &sub.UpdatedAt)
	if err != nil {
		return Submission{}, err
	}
	sub.DuplicateOfCaseID = duplicate.String
	sub.AllowedUses = stringSlice(allowed)
	if rawExpiresAt.Valid {
		sub.RawExpiresAt = &rawExpiresAt.Time
	}
	if rawDeletedAt.Valid {
		sub.RawDeletedAt = &rawDeletedAt.Time
	}
	return sub, nil
}

func scanCase(scanner interface{ Scan(dest ...any) error }) (Case, error) {
	var c Case
	var longHorizon sql.NullString
	var claimedBy sql.NullString
	var claimedAt sql.NullTime
	err := scanner.Scan(&c.ID, &c.SubmissionID, &c.OwnerID, &c.Status, &c.RedactedText, &c.RawHash, &c.CanonicalHash, &c.DRL, &c.CommercialReady, &c.RedactionJSON, &c.AnnotationJSON, &c.QualityGateJSON, &longHorizon, &claimedBy, &claimedAt, &c.CreatedAt, &c.UpdatedAt)
	if err != nil {
		return Case{}, err
	}
	c.LongHorizonJSON = longHorizon.String
	c.ReviewClaimedBy = claimedBy.String
	if claimedAt.Valid {
		c.ReviewClaimedAt = &claimedAt.Time
	}
	return c, nil
}

func scanCases(rows *sql.Rows) ([]Case, error) {
	var cases []Case
	for rows.Next() {
		c, err := scanCase(rows)
		if err != nil {
			return nil, err
		}
		cases = append(cases, c)
	}
	return cases, rows.Err()
}

func scanJob(scanner interface{ Scan(dest ...any) error }) (Job, error) {
	var job Job
	var availableAt sql.NullTime
	var lockedAt sql.NullTime
	err := scanner.Scan(&job.ID, &job.SubmissionID, &job.QueueName, &job.JobType, &job.Status, &job.PayloadJSON, &job.Attempts, &job.MaxAttempts, &job.Error, &availableAt, &lockedAt, &job.LockedBy, &job.CreatedAt, &job.UpdatedAt)
	if err != nil {
		return Job{}, err
	}
	if availableAt.Valid {
		job.AvailableAt = &availableAt.Time
	}
	if lockedAt.Valid {
		job.LockedAt = &lockedAt.Time
	}
	return job, nil
}

func scanDataset(scanner interface{ Scan(dest ...any) error }) (Dataset, error) {
	var dataset Dataset
	var caseIDs string
	err := scanner.Scan(&dataset.ID, &dataset.Name, &dataset.Status, &dataset.Purpose, &dataset.MinDRL, &caseIDs, &dataset.CreatedAt, &dataset.UpdatedAt)
	if err != nil {
		return Dataset{}, err
	}
	dataset.CaseIDs = stringSlice(caseIDs)
	return dataset, nil
}

func scanDatasetArtifact(scanner interface{ Scan(dest ...any) error }) (DatasetArtifact, error) {
	var artifact DatasetArtifact
	err := scanner.Scan(&artifact.ID, &artifact.DatasetID, &artifact.ArtifactType, &artifact.ObjectURI, &artifact.ContentType, &artifact.ByteSize, &artifact.CreatedAt)
	return artifact, err
}

func caseSelectSQL(suffix string) string {
	base := `
		SELECT id, submission_id, owner_id, status, redacted_text, raw_hash, canonical_hash, drl, commercial_ready,
			CAST(redaction_json AS CHAR), CAST(annotation_json AS CHAR), CAST(quality_gate_json AS CHAR),
			CAST(long_horizon_json AS CHAR), COALESCE(review_claimed_by, ''), review_claimed_at, created_at, updated_at
		FROM cases
	`
	return base + " " + suffix
}

func nowUTC() time.Time {
	return time.Now().UTC().Truncate(time.Microsecond)
}

func truncate(value string, limit int) string {
	value = strings.TrimSpace(value)
	if len(value) <= limit {
		return value
	}
	return value[:limit]
}

var schemaStatements = []string{
	`CREATE TABLE IF NOT EXISTS schema_migrations (
		id VARCHAR(128) PRIMARY KEY,
		checksum CHAR(64) NOT NULL,
		description VARCHAR(255) NOT NULL,
		status VARCHAR(32) NOT NULL,
		applied_at DATETIME(6) NOT NULL,
		created_at DATETIME(6) NOT NULL
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS users (
		id VARCHAR(64) PRIMARY KEY,
		email VARCHAR(320) NOT NULL,
		display_name VARCHAR(128) NOT NULL,
		role VARCHAR(32) NOT NULL,
		status VARCHAR(32) NOT NULL,
		password_hash CHAR(64) NOT NULL,
		password_salt CHAR(32) NOT NULL,
		password_algorithm VARCHAR(32) NOT NULL,
		last_login_at DATETIME(6) NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		UNIQUE KEY uniq_users_email (email),
		KEY idx_users_role_status_created (role, status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS auth_tokens (
		id VARCHAR(64) PRIMARY KEY,
		user_id VARCHAR(64) NOT NULL,
		token_hash CHAR(64) NOT NULL,
		token_suffix VARCHAR(16) NOT NULL,
		role VARCHAR(32) NOT NULL,
		status VARCHAR(32) NOT NULL,
		created_by VARCHAR(128) NOT NULL,
		expires_at DATETIME(6) NULL,
		revoked_at DATETIME(6) NULL,
		last_used_at DATETIME(6) NULL,
		created_at DATETIME(6) NOT NULL,
		UNIQUE KEY uniq_auth_tokens_hash (token_hash),
		KEY idx_auth_tokens_user_status_created (user_id, status, created_at),
		KEY idx_auth_tokens_status_expires (status, expires_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS submissions (
		id VARCHAR(64) PRIMARY KEY,
		owner_id VARCHAR(128) NOT NULL,
		source_type VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		raw_object_uri TEXT NOT NULL,
		raw_hash CHAR(64) NOT NULL,
		duplicate_of_case_id VARCHAR(64) NULL,
		allowed_uses_json JSON NOT NULL,
		raw_expires_at DATETIME(6) NULL,
		raw_deleted_at DATETIME(6) NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_submissions_owner_created (owner_id, created_at),
		KEY idx_submissions_status_created (status, created_at),
		KEY idx_submissions_duplicate (duplicate_of_case_id)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS cases (
		id VARCHAR(64) PRIMARY KEY,
		submission_id VARCHAR(64) NOT NULL,
		owner_id VARCHAR(128) NOT NULL,
		status VARCHAR(32) NOT NULL,
		redacted_text MEDIUMTEXT NOT NULL,
		raw_hash CHAR(64) NOT NULL,
		canonical_hash CHAR(64) NOT NULL,
		drl VARCHAR(16) NOT NULL,
		commercial_ready BOOLEAN NOT NULL DEFAULT FALSE,
		redaction_json JSON NOT NULL,
		annotation_json JSON NOT NULL,
		quality_gate_json JSON NOT NULL,
		long_horizon_json JSON NULL,
		review_claimed_by VARCHAR(128) NULL,
		review_claimed_at DATETIME(6) NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		UNIQUE KEY uniq_cases_submission (submission_id),
		UNIQUE KEY uniq_cases_canonical_hash (canonical_hash),
		KEY idx_cases_owner_created (owner_id, created_at),
		KEY idx_cases_status_created (status, created_at),
		KEY idx_cases_drl_ready (drl, commercial_ready, updated_at),
		KEY idx_cases_review_claim (review_claimed_by, review_claimed_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS jobs (
		id VARCHAR(64) PRIMARY KEY,
		submission_id VARCHAR(64) NOT NULL,
		queue_name VARCHAR(64) NOT NULL,
		job_type VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		payload_json JSON NOT NULL,
		attempts INT NOT NULL DEFAULT 0,
		max_attempts INT NOT NULL DEFAULT 5,
		error VARCHAR(1200) NOT NULL DEFAULT '',
		available_at DATETIME(6) NULL,
		locked_at DATETIME(6) NULL,
		locked_by VARCHAR(128) NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_jobs_submission_created (submission_id, created_at),
		KEY idx_jobs_queue_status (queue_name, status, available_at),
		KEY idx_jobs_locked (locked_by, locked_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS assets (
		id VARCHAR(64) PRIMARY KEY,
		owner_id VARCHAR(128) NOT NULL,
		submission_id VARCHAR(64) NULL,
		authorization_snapshot_id VARCHAR(64) NULL,
		filename VARCHAR(255) NOT NULL,
		media_type VARCHAR(128) NOT NULL,
		asset_type VARCHAR(64) NOT NULL,
		byte_size BIGINT NOT NULL,
		object_uri TEXT NOT NULL,
		status VARCHAR(32) NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_assets_owner_created (owner_id, created_at),
		KEY idx_assets_status_created (status, created_at),
		KEY idx_assets_submission (submission_id)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS datasets (
		id VARCHAR(64) PRIMARY KEY,
		name VARCHAR(255) NOT NULL,
		status VARCHAR(32) NOT NULL,
		purpose VARCHAR(64) NOT NULL,
		min_drl VARCHAR(16) NOT NULL,
		case_ids_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_datasets_status_created (status, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS dataset_artifacts (
		id VARCHAR(64) PRIMARY KEY,
		dataset_id VARCHAR(64) NOT NULL,
		artifact_type VARCHAR(64) NOT NULL,
		object_uri TEXT NOT NULL,
		content_type VARCHAR(128) NOT NULL,
		byte_size BIGINT NOT NULL,
		created_at DATETIME(6) NOT NULL,
		UNIQUE KEY uniq_dataset_artifact (dataset_id, artifact_type),
		KEY idx_dataset_artifacts_dataset (dataset_id)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS reviews (
		id VARCHAR(64) PRIMARY KEY,
		case_id VARCHAR(64) NOT NULL,
		reviewer_id VARCHAR(128) NOT NULL,
		review_type VARCHAR(64) NOT NULL,
		decision VARCHAR(32) NOT NULL,
		score DOUBLE NOT NULL DEFAULT 0,
		notes TEXT NOT NULL,
		evidence_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		KEY idx_reviews_case_created (case_id, created_at),
		KEY idx_reviews_decision_created (decision, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS audit_logs (
		id VARCHAR(64) PRIMARY KEY,
		actor_id VARCHAR(128) NOT NULL,
		event_type VARCHAR(128) NOT NULL,
		entity_type VARCHAR(64) NOT NULL,
		entity_id VARCHAR(128) NOT NULL,
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		KEY idx_audit_entity_created (entity_type, entity_id, created_at),
		KEY idx_audit_actor_created (actor_id, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
	`CREATE TABLE IF NOT EXISTS records (
		id VARCHAR(64) PRIMARY KEY,
		record_type VARCHAR(64) NOT NULL,
		status VARCHAR(32) NOT NULL,
		owner_id VARCHAR(128) NULL,
		parent_id VARCHAR(128) NULL,
		payload_json JSON NOT NULL,
		created_at DATETIME(6) NOT NULL,
		updated_at DATETIME(6) NOT NULL,
		KEY idx_records_type_created (record_type, created_at),
		KEY idx_records_type_status_created (record_type, status, created_at),
		KEY idx_records_type_owner_created (record_type, owner_id, created_at),
		KEY idx_records_type_parent_created (record_type, parent_id, created_at)
	) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`,
}
