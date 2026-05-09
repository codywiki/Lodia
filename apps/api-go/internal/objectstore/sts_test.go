package objectstore

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/codywiki/lodia/apps/api-go/internal/config"
)

func TestDirectUploadPrefixScopesToGlobalPrefix(t *testing.T) {
	if got := directUploadPrefix("lodia", "raw/user-a"); got != "lodia/user-a/" {
		t.Fatalf("unexpected prefix %q", got)
	}
	if got := directUploadPrefix("lodia", "datasets/export"); got != "lodia/datasets/export/" {
		t.Fatalf("unexpected prefix %q", got)
	}
	if got := directUploadPrefix("lodia", "lodia/direct"); got != "lodia/direct/" {
		t.Fatalf("unexpected prefix %q", got)
	}
	if got := directUploadPrefix("lodia", "../raw/escape"); got != "lodia/escape/" {
		t.Fatalf("unexpected traversal-cleaned prefix %q", got)
	}
}

func TestUploadPolicyRestrictsPrefix(t *testing.T) {
	policy := uploadPolicy("bucket-a", "lodia/direct/")
	stmt := policy["Statement"].([]map[string]any)[0]
	resources := stmt["Resource"].([]string)
	if len(resources) != 1 || resources[0] != "acs:oss:*:*:bucket-a/lodia/direct/*" {
		t.Fatalf("unexpected resources %#v", resources)
	}
}

func TestClampTTL(t *testing.T) {
	if got := clampTTL(30, 0); got != 900 {
		t.Fatalf("short ttl should be raised to OSS STS minimum, got %d", got)
	}
	if got := clampTTL(7200, 0); got != 3600 {
		t.Fatalf("long ttl should be capped, got %d", got)
	}
	if got := clampTTL(0, 1200); got != 1200 {
		t.Fatalf("configured ttl should be used, got %d", got)
	}
}

func TestSTSSignatureIsStable(t *testing.T) {
	values := map[string]string{
		"AccessKeyId":      "testid",
		"Action":           "AssumeRole",
		"DurationSeconds":  "900",
		"Format":           "JSON",
		"RoleArn":          "acs:ram::123:role/demo",
		"RoleSessionName":  "lodia-upload",
		"SignatureMethod":  "HMAC-SHA1",
		"SignatureNonce":   "nonce",
		"SignatureVersion": "1.0",
		"Timestamp":        time.Date(2026, 5, 8, 0, 0, 0, 0, time.UTC).Format("2006-01-02T15:04:05Z"),
		"Version":          "2015-04-01",
	}
	sig := signSTS(values, "testsecret")
	if sig == "" || strings.Contains(sig, " ") {
		t.Fatalf("unexpected signature %q", sig)
	}
	if signSTS(values, "testsecret") != sig {
		t.Fatalf("signature should be stable")
	}
}

func TestTemporaryUploadCredentialsLocalModeUsesServerUpload(t *testing.T) {
	creds, err := TemporaryUploadCredentials(context.Background(), config.Config{
		ObjectBackend:         "local",
		OSSPrefix:             "lodia",
		OSSSTSDurationSeconds: 900,
	}, "raw/smoke", 60)
	if err != nil {
		t.Fatal(err)
	}
	if creds.Supported || creds.Reason != "local_object_storage_uses_server_upload" || creds.CredentialsMode != "server_upload_only" {
		t.Fatalf("unexpected local credentials response %#v", creds)
	}
	if creds.KeyPrefix != "lodia/smoke/" || creds.ExpiresInSeconds != 900 {
		t.Fatalf("unexpected local upload scope %#v", creds)
	}
}

func TestTemporaryUploadCredentialsOSSDisabledDoesNotCallSTS(t *testing.T) {
	creds, err := TemporaryUploadCredentials(context.Background(), config.Config{
		ObjectBackend:         "oss",
		OSSEndpoint:           "https://oss-cn.example.aliyuncs.com",
		OSSBucket:             "bucket-a",
		OSSAccessKey:          "ak",
		OSSSecretKey:          "sk",
		OSSPrefix:             "lodia",
		OSSSTSDurationSeconds: 900,
	}, "uploads", 1200)
	if err != nil {
		t.Fatal(err)
	}
	if creds.Supported || creds.Reason != "oss_sts_disabled" || creds.KeyPrefix != "lodia/uploads/" {
		t.Fatalf("unexpected disabled STS response %#v", creds)
	}
}
