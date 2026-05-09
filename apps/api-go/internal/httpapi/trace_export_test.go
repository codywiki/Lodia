package httpapi

import (
	"strings"
	"testing"
)

func TestTraceExportTextNormalizesLongHorizonFields(t *testing.T) {
	text := traceExportText(traceExportRequest{
		Title:  "Release repair",
		Source: "codex",
		Trace: map[string]any{
			"objective":      "fix the failing release",
			"context":        []any{"CI failed after a ledger change"},
			"constraints":    []any{"preserve unrelated edits", "do not leak secrets"},
			"steps":          []any{"inspect logs", "patch code", "rerun smoke"},
			"tool_results":   "go test ./... passed",
			"failures":       "first patch missed settlement state",
			"corrections":    "added settled ledger assertion",
			"acceptance":     "smoke passed",
			"reusable_rules": "capture objective, evidence, correction and acceptance",
		},
	})
	for _, expected := range []string{"Title: Release repair", "Goal: fix the failing release", "Plan: inspect logs; patch code; rerun smoke", "Reusable rule: capture objective"} {
		if !strings.Contains(text, expected) {
			t.Fatalf("trace export text missing %q:\n%s", expected, text)
		}
	}
}

func TestAuthorizationIDForOwnerKeepsDemoCompatibility(t *testing.T) {
	if got := authorizationIDForOwner("demo_contributor"); got != "auth_demo" {
		t.Fatalf("demo contributor auth id = %q", got)
	}
	if got := authorizationIDForOwner("smoke_owner"); !strings.HasPrefix(got, "auth_") || got == "auth_demo" {
		t.Fatalf("owner-specific auth id = %q", got)
	}
}
