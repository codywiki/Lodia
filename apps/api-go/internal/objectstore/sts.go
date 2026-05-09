package objectstore

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"path"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/codywiki/lodia/apps/api-go/internal/config"
)

type UploadCredentials struct {
	Supported        bool              `json:"supported"`
	Backend          string            `json:"backend"`
	Reason           string            `json:"reason,omitempty"`
	Bucket           string            `json:"bucket,omitempty"`
	Endpoint         string            `json:"endpoint,omitempty"`
	KeyPrefix        string            `json:"key_prefix,omitempty"`
	ExpiresAt        string            `json:"expires_at,omitempty"`
	ExpiresInSeconds int               `json:"expires_in_seconds"`
	CredentialsMode  string            `json:"credentials_mode"`
	UploadEndpoint   string            `json:"upload_endpoint,omitempty"`
	Credentials      map[string]string `json:"credentials,omitempty"`
	Policy           map[string]any    `json:"policy,omitempty"`
}

func TemporaryUploadCredentials(ctx context.Context, cfg config.Config, requestedPrefix string, requestedTTL int) (UploadCredentials, error) {
	ttl := clampTTL(requestedTTL, cfg.OSSSTSDurationSeconds)
	keyPrefix := directUploadPrefix(cfg.OSSPrefix, requestedPrefix)
	expiresAt := time.Now().UTC().Add(time.Duration(ttl) * time.Second).Format(time.RFC3339)
	base := UploadCredentials{
		Supported:        false,
		Backend:          cfg.ObjectBackend,
		Bucket:           cfg.OSSBucket,
		Endpoint:         strings.TrimRight(cfg.OSSEndpoint, "/"),
		KeyPrefix:        keyPrefix,
		ExpiresAt:        expiresAt,
		ExpiresInSeconds: ttl,
		CredentialsMode:  "server_upload_only",
		UploadEndpoint:   "/api/assets",
	}
	if !strings.EqualFold(cfg.ObjectBackend, "oss") {
		base.Reason = "local_object_storage_uses_server_upload"
		return base, nil
	}
	if !cfg.OSSSTSEnabled {
		base.Reason = "oss_sts_disabled"
		return base, nil
	}
	if cfg.OSSBucket == "" || cfg.OSSEndpoint == "" || cfg.OSSAccessKey == "" || cfg.OSSSecretKey == "" || cfg.OSSSTSRoleARN == "" {
		base.Reason = "oss_sts_config_required"
		return base, nil
	}
	policy := uploadPolicy(cfg.OSSBucket, keyPrefix)
	credentials, err := assumeRole(ctx, cfg, ttl, policy)
	if err != nil {
		return UploadCredentials{}, err
	}
	base.Supported = true
	base.Reason = ""
	base.CredentialsMode = "sts_assume_role"
	base.UploadEndpoint = ""
	base.ExpiresAt = credentials.Expiration
	base.ExpiresInSeconds = ttl
	base.Credentials = map[string]string{
		"access_key_id":     credentials.AccessKeyID,
		"access_key_secret": credentials.AccessKeySecret,
		"security_token":    credentials.SecurityToken,
	}
	base.Policy = policy
	return base, nil
}

type assumeRoleResponse struct {
	Credentials struct {
		AccessKeyID     string `json:"AccessKeyId"`
		AccessKeySecret string `json:"AccessKeySecret"`
		SecurityToken   string `json:"SecurityToken"`
		Expiration      string `json:"Expiration"`
	} `json:"Credentials"`
}

type stsCredentials struct {
	AccessKeyID     string
	AccessKeySecret string
	SecurityToken   string
	Expiration      string
}

func assumeRole(ctx context.Context, cfg config.Config, ttl int, policy map[string]any) (stsCredentials, error) {
	policyJSON, err := json.Marshal(policy)
	if err != nil {
		return stsCredentials{}, err
	}
	values := map[string]string{
		"AccessKeyId":      cfg.OSSAccessKey,
		"Action":           "AssumeRole",
		"DurationSeconds":  strconv.Itoa(ttl),
		"Format":           "JSON",
		"Policy":           string(policyJSON),
		"RoleArn":          cfg.OSSSTSRoleARN,
		"RoleSessionName":  cleanRoleSessionName(cfg.OSSSTSSessionName),
		"SignatureMethod":  "HMAC-SHA1",
		"SignatureNonce":   randomNonce(),
		"SignatureVersion": "1.0",
		"Timestamp":        time.Now().UTC().Format("2006-01-02T15:04:05Z"),
		"Version":          "2015-04-01",
	}
	values["Signature"] = signSTS(values, cfg.OSSSecretKey)
	endpoint := strings.TrimRight(cfg.OSSSTSEndpoint, "/")
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint+"/?"+encodeQuery(values), nil)
	if err != nil {
		return stsCredentials{}, err
	}
	resp, err := (&http.Client{Timeout: 15 * time.Second}).Do(req)
	if err != nil {
		return stsCredentials{}, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return stsCredentials{}, err
	}
	if resp.StatusCode >= 300 {
		return stsCredentials{}, fmt.Errorf("sts_assume_role_failed:%d", resp.StatusCode)
	}
	var out assumeRoleResponse
	if err := json.Unmarshal(body, &out); err != nil {
		return stsCredentials{}, err
	}
	if out.Credentials.AccessKeyID == "" || out.Credentials.AccessKeySecret == "" || out.Credentials.SecurityToken == "" {
		return stsCredentials{}, errors.New("sts_credentials_missing")
	}
	return stsCredentials{
		AccessKeyID:     out.Credentials.AccessKeyID,
		AccessKeySecret: out.Credentials.AccessKeySecret,
		SecurityToken:   out.Credentials.SecurityToken,
		Expiration:      out.Credentials.Expiration,
	}, nil
}

func uploadPolicy(bucket string, keyPrefix string) map[string]any {
	resource := "acs:oss:*:*:" + bucket + "/" + strings.TrimLeft(keyPrefix, "/") + "*"
	return map[string]any{
		"Version": "1",
		"Statement": []map[string]any{
			{
				"Effect":   "Allow",
				"Action":   []string{"oss:PutObject", "oss:AbortMultipartUpload", "oss:ListParts"},
				"Resource": []string{resource},
			},
		},
	}
}

func signSTS(values map[string]string, secret string) string {
	canonical := canonicalQuery(values)
	stringToSign := "GET&%2F&" + percentEncode(canonical)
	mac := hmac.New(sha1.New, []byte(secret+"&"))
	_, _ = mac.Write([]byte(stringToSign))
	return base64.StdEncoding.EncodeToString(mac.Sum(nil))
}

func canonicalQuery(values map[string]string) string {
	keys := make([]string, 0, len(values))
	for key := range values {
		if key != "Signature" {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		parts = append(parts, percentEncode(key)+"="+percentEncode(values[key]))
	}
	return strings.Join(parts, "&")
}

func encodeQuery(values map[string]string) string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		parts = append(parts, percentEncode(key)+"="+percentEncode(values[key]))
	}
	return strings.Join(parts, "&")
}

func percentEncode(value string) string {
	encoded := url.QueryEscape(value)
	encoded = strings.ReplaceAll(encoded, "+", "%20")
	encoded = strings.ReplaceAll(encoded, "*", "%2A")
	encoded = strings.ReplaceAll(encoded, "%7E", "~")
	return encoded
}

func directUploadPrefix(globalPrefix string, requestedPrefix string) string {
	requested := strings.Trim(strings.TrimPrefix(path.Clean("/"+requestedPrefix), "/"), "/")
	if requested == "" || requested == "." {
		requested = "direct"
	}
	requested = strings.TrimPrefix(requested, "raw/")
	global := cleanPrefix(globalPrefix)
	if global == "" {
		return requested + "/"
	}
	if strings.HasPrefix(requested, global+"/") {
		return requested + "/"
	}
	return global + "/" + requested + "/"
}

func clampTTL(requested int, configured int) int {
	ttl := requested
	if ttl <= 0 {
		ttl = configured
	}
	if ttl <= 0 {
		ttl = 900
	}
	if ttl < 900 {
		return 900
	}
	if ttl > 3600 {
		return 3600
	}
	return ttl
}

func cleanRoleSessionName(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return "lodia-upload"
	}
	var out strings.Builder
	for _, r := range value {
		if r == '-' || r == '_' || r == '.' || r >= '0' && r <= '9' || r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' {
			out.WriteRune(r)
		}
	}
	clean := out.String()
	if clean == "" {
		return "lodia-upload"
	}
	if len(clean) > 64 {
		return clean[:64]
	}
	return clean
}

func randomNonce() string {
	var buf [16]byte
	if _, err := rand.Read(buf[:]); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 10)
	}
	return hex.EncodeToString(buf[:])
}
