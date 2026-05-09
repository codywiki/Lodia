package store

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/hex"
	"errors"
	"strings"
	"time"
)

var ErrInvalidCredential = errors.New("invalid_credentials")

type User struct {
	ID                string
	Email             string
	DisplayName       string
	Role              string
	Status            string
	PasswordHash      string
	PasswordSalt      string
	PasswordAlgorithm string
	LastLoginAt       *time.Time
	CreatedAt         time.Time
	UpdatedAt         time.Time
}

type AuthToken struct {
	ID          string
	UserID      string
	TokenHash   string
	TokenSuffix string
	Role        string
	Status      string
	CreatedBy   string
	ExpiresAt   *time.Time
	RevokedAt   *time.Time
	LastUsedAt  *time.Time
	CreatedAt   time.Time
}

type TokenPrincipal struct {
	UserID  string
	Email   string
	Role    string
	TokenID string
}

func (db *DB) CreateUser(ctx context.Context, user *User, password string, pepper string) error {
	now := nowUTC()
	if user.ID == "" {
		user.ID = NewID("user")
	}
	user.Email = strings.ToLower(strings.TrimSpace(user.Email))
	if user.DisplayName == "" {
		user.DisplayName = user.Email
	}
	user.Role = normalizeRole(user.Role)
	if user.Status == "" {
		user.Status = "active"
	}
	user.PasswordSalt = randomHex(16)
	user.PasswordAlgorithm = "sha256_salt_v1"
	user.PasswordHash = passwordHash(password, user.PasswordSalt, pepper)
	user.CreatedAt = now
	user.UpdatedAt = now
	_, err := db.sql.ExecContext(ctx, `
		INSERT INTO users (id, email, display_name, role, status, password_hash, password_salt, password_algorithm, last_login_at, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
	`, user.ID, user.Email, user.DisplayName, user.Role, user.Status, user.PasswordHash, user.PasswordSalt, user.PasswordAlgorithm, user.CreatedAt, user.UpdatedAt)
	return err
}

func (db *DB) ListUsers(ctx context.Context, limit int) ([]User, error) {
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := db.sql.QueryContext(ctx, `
		SELECT id, email, display_name, role, status, password_hash, password_salt, password_algorithm, last_login_at, created_at, updated_at
		FROM users ORDER BY created_at DESC LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var users []User
	for rows.Next() {
		user, err := scanUser(rows)
		if err != nil {
			return nil, err
		}
		users = append(users, user)
	}
	return users, rows.Err()
}

func (db *DB) GetUser(ctx context.Context, id string) (User, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, email, display_name, role, status, password_hash, password_salt, password_algorithm, last_login_at, created_at, updated_at
		FROM users WHERE id = ?
	`, id)
	return scanUser(row)
}

func (db *DB) GetUserByEmail(ctx context.Context, email string) (User, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, email, display_name, role, status, password_hash, password_salt, password_algorithm, last_login_at, created_at, updated_at
		FROM users WHERE email = ?
	`, strings.ToLower(strings.TrimSpace(email)))
	return scanUser(row)
}

func (db *DB) AuthenticateUser(ctx context.Context, email string, password string, pepper string) (User, error) {
	user, err := db.GetUserByEmail(ctx, email)
	if err != nil {
		return User{}, err
	}
	if user.Status != "active" || strings.TrimSpace(password) == "" {
		return User{}, ErrInvalidCredential
	}
	expected := passwordHash(password, user.PasswordSalt, pepper)
	if subtle.ConstantTimeCompare([]byte(expected), []byte(user.PasswordHash)) != 1 {
		return User{}, ErrInvalidCredential
	}
	_, _ = db.sql.ExecContext(ctx, `UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?`, nowUTC(), nowUTC(), user.ID)
	return user, nil
}

func (db *DB) IssueAuthToken(ctx context.Context, userID string, createdBy string, ttl time.Duration) (AuthToken, string, error) {
	user, err := db.GetUser(ctx, userID)
	if err != nil {
		return AuthToken{}, "", err
	}
	rawToken := "lod_pat_" + randomHex(32)
	now := nowUTC()
	var expiresAt *time.Time
	if ttl > 0 {
		value := now.Add(ttl).Truncate(time.Microsecond)
		expiresAt = &value
	}
	token := AuthToken{
		ID:          NewID("token"),
		UserID:      user.ID,
		TokenHash:   tokenHash(rawToken),
		TokenSuffix: suffixString(rawToken, 8),
		Role:        user.Role,
		Status:      "active",
		CreatedBy:   createdBy,
		ExpiresAt:   expiresAt,
		CreatedAt:   now,
	}
	_, err = db.sql.ExecContext(ctx, `
		INSERT INTO auth_tokens (id, user_id, token_hash, token_suffix, role, status, created_by, expires_at, revoked_at, last_used_at, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
	`, token.ID, token.UserID, token.TokenHash, token.TokenSuffix, token.Role, token.Status, token.CreatedBy, token.ExpiresAt, token.CreatedAt)
	return token, rawToken, err
}

func (db *DB) RevokeAuthToken(ctx context.Context, id string) (AuthToken, error) {
	now := nowUTC()
	_, err := db.sql.ExecContext(ctx, `UPDATE auth_tokens SET status = 'revoked', revoked_at = ? WHERE id = ?`, now, id)
	if err != nil {
		return AuthToken{}, err
	}
	return db.GetAuthToken(ctx, id)
}

func (db *DB) GetAuthToken(ctx context.Context, id string) (AuthToken, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT id, user_id, token_hash, token_suffix, role, status, created_by, expires_at, revoked_at, last_used_at, created_at
		FROM auth_tokens WHERE id = ?
	`, id)
	return scanAuthToken(row)
}

func (db *DB) LookupToken(ctx context.Context, rawToken string) (TokenPrincipal, error) {
	row := db.sql.QueryRowContext(ctx, `
		SELECT t.id, u.id, u.email, u.role
		FROM auth_tokens t
		JOIN users u ON u.id = t.user_id
		WHERE t.token_hash = ?
			AND t.status = 'active'
			AND u.status = 'active'
			AND (t.expires_at IS NULL OR t.expires_at > ?)
	`, tokenHash(rawToken), nowUTC())
	var principal TokenPrincipal
	if err := row.Scan(&principal.TokenID, &principal.UserID, &principal.Email, &principal.Role); err != nil {
		return TokenPrincipal{}, err
	}
	_, _ = db.sql.ExecContext(ctx, `UPDATE auth_tokens SET last_used_at = ? WHERE id = ?`, nowUTC(), principal.TokenID)
	return principal, nil
}

func (db *DB) CountUsers(ctx context.Context, role string, status string) (int64, error) {
	var count int64
	err := db.sql.QueryRowContext(ctx, `SELECT COUNT(*) FROM users WHERE role = ? AND status = ?`, role, status).Scan(&count)
	return count, err
}

func UserPayload(user User) map[string]any {
	return map[string]any{
		"id":            user.ID,
		"email":         user.Email,
		"display_name":  user.DisplayName,
		"role":          user.Role,
		"status":        user.Status,
		"last_login_at": optionalTime(user.LastLoginAt),
		"created_at":    user.CreatedAt,
		"updated_at":    user.UpdatedAt,
	}
}

func AuthTokenPayload(token AuthToken) map[string]any {
	return map[string]any{
		"id":           token.ID,
		"user_id":      token.UserID,
		"token_suffix": token.TokenSuffix,
		"role":         token.Role,
		"status":       token.Status,
		"created_by":   token.CreatedBy,
		"expires_at":   optionalTime(token.ExpiresAt),
		"revoked_at":   optionalTime(token.RevokedAt),
		"last_used_at": optionalTime(token.LastUsedAt),
		"created_at":   token.CreatedAt,
	}
}

func scanUser(scanner interface{ Scan(dest ...any) error }) (User, error) {
	var user User
	var lastLogin sql.NullTime
	if err := scanner.Scan(&user.ID, &user.Email, &user.DisplayName, &user.Role, &user.Status, &user.PasswordHash, &user.PasswordSalt, &user.PasswordAlgorithm, &lastLogin, &user.CreatedAt, &user.UpdatedAt); err != nil {
		return User{}, err
	}
	if lastLogin.Valid {
		user.LastLoginAt = &lastLogin.Time
	}
	return user, nil
}

func scanAuthToken(scanner interface{ Scan(dest ...any) error }) (AuthToken, error) {
	var token AuthToken
	var expiresAt sql.NullTime
	var revokedAt sql.NullTime
	var lastUsedAt sql.NullTime
	if err := scanner.Scan(&token.ID, &token.UserID, &token.TokenHash, &token.TokenSuffix, &token.Role, &token.Status, &token.CreatedBy, &expiresAt, &revokedAt, &lastUsedAt, &token.CreatedAt); err != nil {
		return AuthToken{}, err
	}
	if expiresAt.Valid {
		token.ExpiresAt = &expiresAt.Time
	}
	if revokedAt.Valid {
		token.RevokedAt = &revokedAt.Time
	}
	if lastUsedAt.Valid {
		token.LastUsedAt = &lastUsedAt.Time
	}
	return token, nil
}

func normalizeRole(role string) string {
	switch strings.ToLower(strings.TrimSpace(role)) {
	case "admin", "reviewer", "contributor":
		return strings.ToLower(strings.TrimSpace(role))
	default:
		return "contributor"
	}
}

func passwordHash(password string, salt string, pepper string) string {
	sum := sha256.Sum256([]byte("lodia-password-v1:" + salt + ":" + pepper + ":" + password))
	return hex.EncodeToString(sum[:])
}

func tokenHash(rawToken string) string {
	sum := sha256.Sum256([]byte(rawToken))
	return hex.EncodeToString(sum[:])
}

func randomHex(byteCount int) string {
	buf := make([]byte, byteCount)
	if _, err := rand.Read(buf); err != nil {
		return hex.EncodeToString([]byte(time.Now().Format(time.RFC3339Nano)))[:byteCount]
	}
	return hex.EncodeToString(buf)
}

func suffixString(value string, n int) string {
	if n <= 0 || len(value) <= n {
		return value
	}
	return value[len(value)-n:]
}

func optionalTime(value *time.Time) any {
	if value == nil {
		return nil
	}
	return value.UTC().Format(time.RFC3339)
}
