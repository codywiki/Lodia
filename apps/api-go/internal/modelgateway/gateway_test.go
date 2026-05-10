package modelgateway

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/codywiki/lodia/apps/api-go/internal/config"
	"github.com/codywiki/lodia/apps/api-go/internal/focus"
)

func TestLocalGatewayUsesRedactedTaskOnly(t *testing.T) {
	gateway := New(config.Config{
		ModelGatewayMode:          "local",
		ModelGatewayProviderName:  "local_rules",
		ModelGatewayPromptVersion: focus.SchemaVersion,
	})
	resp := gateway.Annotate(context.Background(), Request{
		Operation:       OperationAnnotation,
		EntityType:      "preview",
		EntityID:        "preview_1",
		RedactedText:    "目标：修复部署。过程：查看日志。验收：ready 通过。规则：先确认数据库连接。",
		DataFocus:       "llm_long_horizon_task",
		RedactionPassed: true,
	})
	if resp.Status != "completed" {
		t.Fatalf("gateway status = %q", resp.Status)
	}
	if resp.InputHash == "" || resp.OutputHash == "" {
		t.Fatalf("hashes should be present: %#v", resp)
	}
	if resp.Metadata["external_transfer"] != false {
		t.Fatalf("local gateway must not mark external transfer: %#v", resp.Metadata)
	}
}

func TestGatewaySkipsWhenRedactionFailed(t *testing.T) {
	resp := New(config.Config{ModelGatewayMode: "http"}).Annotate(context.Background(), Request{
		RedactedText:    "token=[REDACTED_SECRET]",
		RedactionPassed: false,
	})
	if resp.Status != "skipped" || resp.ErrorCode != "redaction_not_passed" {
		t.Fatalf("unexpected response %#v", resp)
	}
}

func TestGatewayHealthBlocksChinaProductionWithoutHTTP(t *testing.T) {
	health := New(config.Config{
		Deployment:               "china_independent",
		ModelGatewayMode:         "local",
		ModelGatewayProviderName: "local_rules",
		ModelGatewayModel:        "lodia-rules-v1",
		ModelGatewayRegion:       "CN",
	}).Health(context.Background())
	if health["ok"] == true {
		t.Fatalf("china production profile must require HTTP model gateway: %#v", health)
	}
}

func TestGatewayHealthRequiresHTTPEndpoint(t *testing.T) {
	health := New(config.Config{
		Deployment:               "production",
		ModelGatewayMode:         "http",
		ModelGatewayProviderName: "domestic_llm",
		ModelGatewayModel:        "approved-model",
		ModelGatewayRegion:       "CN",
	}).Health(context.Background())
	if health["ok"] == true {
		t.Fatalf("HTTP gateway without endpoint must not be ready: %#v", health)
	}
}

func TestHTTPGatewayPostsRedactedPayload(t *testing.T) {
	var sawAuthorization bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") == "Bearer test-key" {
			sawAuthorization = true
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"labels":{"critic":"passed"},"quality_score":0.8,"confidence":0.7,"model":"test-model","metadata":{"route":"unit"}}`))
	}))
	defer server.Close()

	resp := New(config.Config{
		ModelGatewayMode:     "http",
		ModelGatewayEndpoint: server.URL,
		ModelGatewayAPIKey:   "test-key",
		ModelGatewayModel:    "fallback-model",
	}).Annotate(context.Background(), Request{
		RedactedText:    "目标：复盘任务。验收：通过。",
		RedactionPassed: true,
	})
	if !sawAuthorization {
		t.Fatalf("expected bearer auth header")
	}
	if resp.Status != "completed" || resp.Model != "test-model" || resp.Labels["critic"] != "passed" {
		t.Fatalf("unexpected HTTP response %#v", resp)
	}
}

func TestHTTPGatewayHashesActualTruncatedPayload(t *testing.T) {
	var redactedText string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload struct {
			RedactedText string `json:"redacted_text"`
		}
		_ = json.NewDecoder(r.Body).Decode(&payload)
		redactedText = payload.RedactedText
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"labels":{"critic":"passed"},"quality_score":0.8,"confidence":0.7}`))
	}))
	defer server.Close()

	input := "目标：修复一个非常长的长程任务案例，过程包含很多日志和验收规则。"
	resp := New(config.Config{
		ModelGatewayMode:          "http",
		ModelGatewayEndpoint:      server.URL,
		ModelGatewayMaxInputChars: 12,
	}).Annotate(context.Background(), Request{
		RedactedText:    input,
		RedactionPassed: true,
	})
	expected := truncateRunes(input, 12)
	if redactedText != expected {
		t.Fatalf("provider received %q, want %q", redactedText, expected)
	}
	if resp.InputHash != hashString(expected) {
		t.Fatalf("input hash must cover actual provider payload")
	}
	if resp.Metadata["truncated"] != true {
		t.Fatalf("expected truncation metadata, got %#v", resp.Metadata)
	}
}
