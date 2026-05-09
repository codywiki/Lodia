package httpapi

import (
	"context"
	"encoding/json"
	"log"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/codywiki/lodia/apps/api-go/internal/store"
)

type contextKey string

const requestIDKey contextKey = "lodia_request_id"

type responseCapture struct {
	http.ResponseWriter
	status int
	bytes  int
}

func (w *responseCapture) WriteHeader(status int) {
	if w.status != 0 {
		return
	}
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func (w *responseCapture) Write(body []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK
	}
	n, err := w.ResponseWriter.Write(body)
	w.bytes += n
	return n, err
}

func (s *Server) requestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestID := cleanRequestID(r.Header.Get("X-Request-ID"))
		if requestID == "" {
			requestID = store.NewID("req")
		}
		w.Header().Set("X-Request-ID", requestID)
		ctx := context.WithValue(r.Context(), requestIDKey, requestID)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func (s *Server) accessLog(next http.Handler) http.Handler {
	if !s.cfg.AccessLogEnabled {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		capture := &responseCapture{ResponseWriter: w}
		next.ServeHTTP(capture, r)
		status := capture.status
		if status == 0 {
			status = http.StatusOK
		}
		writeStructuredLog(map[string]any{
			"event":       "http_request",
			"request_id":  requestIDFrom(r),
			"method":      r.Method,
			"path":        r.URL.Path,
			"status":      status,
			"bytes":       capture.bytes,
			"duration_ms": time.Since(start).Milliseconds(),
			"remote_ip":   s.clientIP(r),
			"user_agent":  truncateLogValue(r.UserAgent(), 160),
		})
	})
}

func (s *Server) rateLimit(next http.Handler) http.Handler {
	if !s.cfg.RateLimitEnabled || s.limiter == nil {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodOptions || r.URL.Path == "/api/health" {
			next.ServeHTTP(w, r)
			return
		}
		allowed, remaining, resetAfter := s.limiter.Allow(s.clientIP(r))
		w.Header().Set("X-RateLimit-Limit", strconv.Itoa(s.cfg.RateLimitRequests))
		w.Header().Set("X-RateLimit-Remaining", strconv.Itoa(remaining))
		w.Header().Set("X-RateLimit-Reset", strconv.FormatInt(time.Now().Add(resetAfter).Unix(), 10))
		if !allowed {
			w.Header().Set("Retry-After", strconv.Itoa(int(resetAfter.Seconds())+1))
			writeError(w, http.StatusTooManyRequests, "rate_limited")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) clientIP(r *http.Request) string {
	if s.cfg.TrustProxyHeaders {
		if forwarded := strings.TrimSpace(r.Header.Get("X-Forwarded-For")); forwarded != "" {
			parts := strings.Split(forwarded, ",")
			if ip := strings.TrimSpace(parts[0]); ip != "" {
				return ip
			}
		}
		if realIP := strings.TrimSpace(r.Header.Get("X-Real-IP")); realIP != "" {
			return realIP
		}
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err == nil && host != "" {
		return host
	}
	return r.RemoteAddr
}

func requestIDFrom(r *http.Request) string {
	if value, ok := r.Context().Value(requestIDKey).(string); ok {
		return value
	}
	return ""
}

func cleanRequestID(value string) string {
	value = strings.TrimSpace(value)
	if len(value) > 96 {
		return ""
	}
	for _, r := range value {
		if !(r == '-' || r == '_' || r == '.' || r == ':' || r >= '0' && r <= '9' || r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z') {
			return ""
		}
	}
	return value
}

func writeStructuredLog(entry map[string]any) {
	raw, err := json.Marshal(entry)
	if err != nil {
		log.Print(`{"event":"log_marshal_error"}`)
		return
	}
	log.Print(string(raw))
}

func logRequestPanic(r *http.Request, recovered any) {
	writeStructuredLog(map[string]any{
		"event":      "http_panic",
		"request_id": requestIDFrom(r),
		"method":     r.Method,
		"path":       r.URL.Path,
		"panic":      truncateLogValue(anyString(recovered), 240),
	})
}

func truncateLogValue(value string, limit int) string {
	value = strings.TrimSpace(value)
	if len(value) <= limit {
		return value
	}
	return value[:limit]
}

func anyString(value any) string {
	switch v := value.(type) {
	case string:
		return v
	case error:
		return v.Error()
	default:
		raw, _ := json.Marshal(v)
		return string(raw)
	}
}

type rateLimiter struct {
	mu      sync.Mutex
	limit   int
	window  time.Duration
	buckets map[string]rateBucket
}

type rateBucket struct {
	windowStart time.Time
	count       int
}

func newRateLimiter(limit int, window time.Duration) *rateLimiter {
	if limit <= 0 {
		limit = 600
	}
	if window <= 0 {
		window = time.Minute
	}
	return &rateLimiter{limit: limit, window: window, buckets: map[string]rateBucket{}}
}

func (l *rateLimiter) Allow(key string) (bool, int, time.Duration) {
	if key == "" {
		key = "unknown"
	}
	now := time.Now()
	l.mu.Lock()
	defer l.mu.Unlock()
	bucket := l.buckets[key]
	if bucket.windowStart.IsZero() || now.Sub(bucket.windowStart) >= l.window {
		bucket = rateBucket{windowStart: now}
	}
	resetAfter := l.window - now.Sub(bucket.windowStart)
	if resetAfter < 0 {
		resetAfter = l.window
	}
	if bucket.count >= l.limit {
		l.buckets[key] = bucket
		l.pruneLocked(now)
		return false, 0, resetAfter
	}
	bucket.count++
	l.buckets[key] = bucket
	l.pruneLocked(now)
	return true, l.limit - bucket.count, resetAfter
}

func (l *rateLimiter) pruneLocked(now time.Time) {
	if len(l.buckets) < 10000 {
		return
	}
	for key, bucket := range l.buckets {
		if now.Sub(bucket.windowStart) > 2*l.window {
			delete(l.buckets, key)
		}
	}
}
