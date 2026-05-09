package annotation

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/codywiki/lodia/apps/api-go/internal/focus"
	"github.com/codywiki/lodia/apps/api-go/internal/redaction"
)

type Annotation struct {
	Domain       string            `json:"domain"`
	TaskType     string            `json:"task_type"`
	Difficulty   string            `json:"difficulty"`
	QualityScore float64           `json:"quality_score"`
	Confidence   float64           `json:"confidence"`
	ReuseTypes   []string          `json:"reuse_types"`
	Labels       map[string]string `json:"labels"`
}

type QualityGate struct {
	DRL             string            `json:"drl"`
	GateResults     map[string]string `json:"gate_results"`
	AllowedUses     []string          `json:"allowed_uses"`
	BlockedUses     []string          `json:"blocked_uses"`
	RequiredActions []string          `json:"required_actions"`
	CommercialReady bool              `json:"commercial_ready"`
}

type Preview struct {
	CaseID      string           `json:"case_id"`
	Redaction   redaction.Result `json:"redaction"`
	Annotation  Annotation       `json:"annotation"`
	QualityGate QualityGate      `json:"quality_gate"`
}

func BuildPreview(caseID string, text string, allowedUses []string, dataFocus string) Preview {
	redacted := redaction.Redact(text)
	ann := Annotate(redacted.RedactedText, allowedUses, dataFocus)
	gate := Gate(redacted, ann, allowedUses)
	return Preview{CaseID: caseID, Redaction: redacted, Annotation: ann, QualityGate: gate}
}

func Annotate(redactedText string, allowedUses []string, dataFocus string) Annotation {
	workbench := focus.Extract(redactedText)
	quality := workbench.Quality.Score
	labels := map[string]string{
		"data_focus":         firstNonEmpty(dataFocus, "llm_long_horizon_task"),
		"schema":             focus.SchemaVersion,
		"long_horizon_score": formatScore(quality),
		"long_horizon_tier":  workbench.Quality.Tier,
		"long_horizon_gate":  workbench.Quality.Gate,
		"source_chars":       intString(workbench.Evidence.SourceChars),
	}
	if len(workbench.Evidence.Missing) > 0 {
		labels["long_horizon_missing"] = strings.Join(workbench.Evidence.Missing, ",")
	}
	return Annotation{
		Domain:       "software_agent_task",
		TaskType:     "llm_long_horizon_task",
		Difficulty:   difficulty(quality),
		QualityScore: quality,
		Confidence:   confidence(quality),
		ReuseTypes:   reuseTypes(allowedUses),
		Labels:       labels,
	}
}

func Gate(redactionResult redaction.Result, ann Annotation, allowedUses []string) QualityGate {
	gate := QualityGate{
		DRL:             "DRL2",
		GateResults:     map[string]string{},
		AllowedUses:     append([]string{}, allowedUses...),
		BlockedUses:     []string{},
		RequiredActions: []string{},
	}
	if len(gate.AllowedUses) == 0 {
		gate.AllowedUses = []string{"private_library"}
	}
	gate.GateResults["privacy"] = "auto_redacted"
	if !redactionResult.Passed {
		gate.DRL = "DRL0"
		gate.CommercialReady = false
		gate.BlockedUses = append(gate.BlockedUses, "commercial_dataset", "training")
		gate.RequiredActions = append(gate.RequiredActions, "privacy_manual_review")
		return gate
	}
	score := ann.QualityScore
	if score < 0.5 {
		gate.DRL = "DRL1"
		gate.RequiredActions = append(gate.RequiredActions, "long_horizon_evidence_enrichment")
		gate.BlockedUses = append(gate.BlockedUses, "commercial_dataset", "training")
		gate.GateResults["long_horizon_quality"] = "failed"
		return gate
	}
	gate.GateResults["long_horizon_quality"] = "passed"
	if score >= 0.78 {
		gate.DRL = "DRL3"
		gate.CommercialReady = containsAny(allowedUses, "commercial_dataset", "training")
	} else {
		gate.RequiredActions = append(gate.RequiredActions, "human_review")
	}
	return gate
}

func WithLongHorizonRefinement(ann Annotation, gate QualityGate, workbench focus.Workbench, fieldQuality focus.FieldQuality) (Annotation, QualityGate) {
	if ann.Labels == nil {
		ann.Labels = map[string]string{}
	}
	ann.Labels["schema"] = focus.SchemaVersion
	ann.Labels["long_horizon_score"] = formatScore(fieldQuality.Score)
	ann.Labels["long_horizon_tier"] = fieldQuality.Tier
	ann.Labels["long_horizon_gate"] = workbench.Quality.Gate
	ann.Labels["long_horizon_refined"] = "true"
	ann.Labels["long_horizon_missing"] = strings.Join(fieldQuality.Missing, ",")
	ann.QualityScore = fieldQuality.Score
	ann.Confidence = confidence(fieldQuality.Score)
	if fieldQuality.Passed && gate.DRL != "DRL0" {
		gate.DRL = "DRL3"
		gate.CommercialReady = containsAny(gate.AllowedUses, "commercial_dataset", "training")
		gate.GateResults["field_refinement"] = "passed"
		gate.RequiredActions = without(gate.RequiredActions, "long_horizon_evidence_enrichment", "human_review")
	} else if gate.DRL != "DRL0" {
		gate.GateResults["field_refinement"] = "needs_work"
		gate.RequiredActions = appendMissing(gate.RequiredActions, "long_horizon_field_completion")
	}
	return ann, gate
}

func Marshal(value any) string {
	out, err := json.Marshal(value)
	if err != nil {
		return "{}"
	}
	return string(out)
}

func UnmarshalAnnotation(raw string) Annotation {
	var value Annotation
	if err := json.Unmarshal([]byte(raw), &value); err != nil {
		return Annotation{Labels: map[string]string{}}
	}
	if value.Labels == nil {
		value.Labels = map[string]string{}
	}
	return value
}

func UnmarshalGate(raw string) QualityGate {
	var value QualityGate
	if err := json.Unmarshal([]byte(raw), &value); err != nil {
		return QualityGate{DRL: "DRL0", GateResults: map[string]string{}, RequiredActions: []string{"invalid_quality_gate"}}
	}
	if value.GateResults == nil {
		value.GateResults = map[string]string{}
	}
	if value.AllowedUses == nil {
		value.AllowedUses = []string{}
	}
	if value.BlockedUses == nil {
		value.BlockedUses = []string{}
	}
	if value.RequiredActions == nil {
		value.RequiredActions = []string{}
	}
	return value
}

func UnmarshalWorkbench(raw string) focus.Workbench {
	var value focus.Workbench
	if err := json.Unmarshal([]byte(raw), &value); err != nil {
		return focus.Workbench{}
	}
	return value
}

func difficulty(score float64) string {
	switch {
	case score >= 0.78:
		return "hard"
	case score >= 0.5:
		return "medium"
	default:
		return "easy"
	}
}

func confidence(score float64) float64 {
	if score >= 0.78 {
		return 0.86
	}
	if score >= 0.5 {
		return 0.72
	}
	return 0.56
}

func reuseTypes(allowedUses []string) []string {
	out := []string{"evaluation"}
	if containsAny(allowedUses, "training") {
		out = append(out, "training")
	}
	if containsAny(allowedUses, "commercial_dataset") {
		out = append(out, "commercial_dataset")
	}
	return out
}

func containsAny(values []string, targets ...string) bool {
	for _, value := range values {
		for _, target := range targets {
			if value == target {
				return true
			}
		}
	}
	return false
}

func appendMissing(values []string, next string) []string {
	for _, value := range values {
		if value == next {
			return values
		}
	}
	return append(values, next)
}

func without(values []string, remove ...string) []string {
	block := map[string]bool{}
	for _, item := range remove {
		block[item] = true
	}
	out := values[:0]
	for _, value := range values {
		if !block[value] {
			out = append(out, value)
		}
	}
	return out
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func formatScore(value float64) string {
	return strings.TrimRight(strings.TrimRight(fmt.Sprintf("%.2f", value), "0"), ".")
}

func intString(value int) string {
	return fmt.Sprintf("%d", value)
}
