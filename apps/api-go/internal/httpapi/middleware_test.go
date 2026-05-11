package httpapi

import (
	"testing"
	"time"

	"github.com/codywiki/lodia/apps/api-go/internal/config"
)

func TestRateLimiterAllowsThenBlocksWithinWindow(t *testing.T) {
	limiter := newRateLimiter(2, time.Minute)
	if ok, remaining, _ := limiter.Allow("client-a"); !ok || remaining != 1 {
		t.Fatalf("first request ok=%v remaining=%d", ok, remaining)
	}
	if ok, remaining, _ := limiter.Allow("client-a"); !ok || remaining != 0 {
		t.Fatalf("second request ok=%v remaining=%d", ok, remaining)
	}
	if ok, remaining, _ := limiter.Allow("client-a"); ok || remaining != 0 {
		t.Fatalf("third request should be blocked, ok=%v remaining=%d", ok, remaining)
	}
	if ok, remaining, _ := limiter.Allow("client-b"); !ok || remaining != 1 {
		t.Fatalf("different client should have an independent bucket, ok=%v remaining=%d", ok, remaining)
	}
}

func TestCleanRequestID(t *testing.T) {
	if got := cleanRequestID(" req_123-abc.DEF:456 "); got != "req_123-abc.DEF:456" {
		t.Fatalf("unexpected cleaned request id %q", got)
	}
	if got := cleanRequestID("bad id with spaces"); got != "" {
		t.Fatalf("invalid request id should be rejected, got %q", got)
	}
	if got := cleanRequestID(string(make([]byte, 97))); got != "" {
		t.Fatalf("overlong request id should be rejected, got %q", got)
	}
}

func TestAllowedOriginRequiresExplicitOriginsInProductionProfile(t *testing.T) {
	prod := &Server{cfg: config.Config{Deployment: "china_independent"}}
	if got := prod.allowedOrigin("https://app.lodia.cn"); got != "" {
		t.Fatalf("production profile should not wildcard CORS origins, got %q", got)
	}
	dev := &Server{cfg: config.Config{Deployment: "development"}}
	if got := dev.allowedOrigin("https://app.lodia.local"); got != "*" {
		t.Fatalf("development without explicit origins should allow wildcard, got %q", got)
	}
	explicit := &Server{cfg: config.Config{Deployment: "china_independent", AllowedOrigins: []string{"https://app.lodia.cn"}}}
	if got := explicit.allowedOrigin("https://app.lodia.cn"); got != "https://app.lodia.cn" {
		t.Fatalf("explicit production origin should be allowed, got %q", got)
	}
	if got := explicit.allowedOrigin("https://evil.example"); got != "" {
		t.Fatalf("unlisted origin should be rejected, got %q", got)
	}
}
