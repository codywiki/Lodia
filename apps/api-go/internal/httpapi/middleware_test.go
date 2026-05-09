package httpapi

import (
	"testing"
	"time"
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
