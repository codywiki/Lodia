package httpapi

import (
	"testing"

	"github.com/codywiki/lodia/apps/api-go/internal/focus"
	"github.com/codywiki/lodia/apps/api-go/internal/store"
)

func TestDatasetEvaluationReadyRequiresCompletedWithoutCriticalFindings(t *testing.T) {
	ready, critical := datasetEvaluationReady(store.DatasetEvaluation{
		Status:       "completed",
		FindingsJSON: `[{"code":"case_not_human_approved","severity":"warning","count":2}]`,
	})
	if !ready || critical != 0 {
		t.Fatalf("warning-only completed evaluation should be ready, ready=%v critical=%d", ready, critical)
	}

	ready, critical = datasetEvaluationReady(store.DatasetEvaluation{
		Status:       "completed",
		FindingsJSON: `[{"code":"content_safety_missing","severity":"critical","count":1}]`,
	})
	if ready || critical != 1 {
		t.Fatalf("critical finding should block readiness, ready=%v critical=%d", ready, critical)
	}

	ready, critical = datasetEvaluationReady(store.DatasetEvaluation{
		Status:       "blocked",
		FindingsJSON: `[]`,
	})
	if ready || critical == 0 {
		t.Fatalf("non-completed evaluation should block readiness, ready=%v critical=%d", ready, critical)
	}
}

func TestRequiredMissingFieldsUsesLongHorizonRequiredSet(t *testing.T) {
	missing := requiredMissingFields(focus.Workbench{Task: focus.Task{
		"objective":      []string{"fix CI"},
		"steps":          []string{"run tests"},
		"acceptance":     []string{"smoke passes"},
		"reusable_rules": []string{},
	}})
	if len(missing) != 1 || missing[0] != "reusable_rules" {
		t.Fatalf("unexpected missing required fields: %#v", missing)
	}

	missing = requiredMissingFields(focus.Workbench{Task: focus.Task{
		"objective":      []string{"   "},
		"steps":          []string{"run tests"},
		"acceptance":     []string{"smoke passes"},
		"reusable_rules": []string{"reuse the verification record"},
	}})
	if len(missing) != 1 || missing[0] != "objective" {
		t.Fatalf("blank required field should be treated as missing: %#v", missing)
	}
}

func TestDRLRankOrdering(t *testing.T) {
	if drlRank("DRL5") <= drlRank("DRL3") {
		t.Fatalf("DRL5 should rank above DRL3")
	}
	if drlRank("unknown") != 0 {
		t.Fatalf("unknown DRL should rank as 0")
	}
}

func TestHoldoutIsolationRequired(t *testing.T) {
	cases := []struct {
		name   string
		target string
		other  string
		want   bool
	}{
		{name: "gold eval cannot overlap commercial", target: "gold_eval", other: "commercial_dataset", want: true},
		{name: "training cannot overlap eval", target: "training", other: "model_eval", want: true},
		{name: "commercial datasets can share cases across versions", target: "commercial_dataset", other: "commercial_dataset", want: false},
		{name: "case library is not a holdout split", target: "case_library", other: "commercial_dataset", want: false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := holdoutIsolationRequired(tc.target, tc.other); got != tc.want {
				t.Fatalf("holdoutIsolationRequired(%q, %q)=%v want %v", tc.target, tc.other, got, tc.want)
			}
		})
	}
}

func TestMissingRequiredComplianceTasks(t *testing.T) {
	completed := map[string]int64{}
	for _, taskType := range requiredProductionComplianceTasks() {
		completed[taskType] = 1
	}
	delete(completed, "backup_restore_drill")
	delete(completed, "payout_tax_policy")
	missing := missingRequiredComplianceTasks(completed)
	if len(missing) != 2 || missing[0] != "backup_restore_drill" || missing[1] != "payout_tax_policy" {
		t.Fatalf("unexpected missing compliance tasks: %#v", missing)
	}
}

func TestAllowedDatasetArtifact(t *testing.T) {
	for _, artifactType := range []string{"data", "manifest", "quality_report", "data_contract"} {
		if !allowedDatasetArtifact(artifactType) {
			t.Fatalf("expected artifact %q to be deliverable", artifactType)
		}
	}
	if allowedDatasetArtifact("../secret") {
		t.Fatalf("path-like artifact name should not be deliverable")
	}
}
