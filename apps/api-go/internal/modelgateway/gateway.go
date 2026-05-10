package modelgateway

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/codywiki/lodia/apps/api-go/internal/config"
	"github.com/codywiki/lodia/apps/api-go/internal/focus"
)

const (
	OperationAnnotation = "llm_long_horizon_annotation"
	SchemaVersion       = "model_gateway_call.v1"
)

type Gateway struct {
	cfg    config.Config
	client *http.Client
}

type Request struct {
	Operation       string          `json:"operation"`
	EntityType      string          `json:"entity_type"`
	EntityID        string          `json:"entity_id"`
	RedactedText    string          `json:"redacted_text"`
	AllowedUses     []string        `json:"allowed_uses"`
	DataFocus       string          `json:"data_focus"`
	Workbench       focus.Workbench `json:"workbench"`
	RedactionPassed bool            `json:"redaction_passed"`
}

type Response struct {
	ProviderType       string            `json:"provider_type"`
	ProviderName       string            `json:"provider_name"`
	Mode               string            `json:"mode"`
	Region             string            `json:"region"`
	Model              string            `json:"model"`
	PromptVersion      string            `json:"prompt_version"`
	Status             string            `json:"status"`
	ErrorCode          string            `json:"error_code,omitempty"`
	InputHash          string            `json:"input_hash"`
	OutputHash         string            `json:"output_hash"`
	LatencyMS          int64             `json:"latency_ms"`
	InputTokens        int               `json:"input_tokens"`
	OutputTokens       int               `json:"output_tokens"`
	CostMicros         int64             `json:"cost_micros"`
	Labels             map[string]string `json:"labels"`
	QualityScore       float64           `json:"quality_score"`
	Confidence         float64           `json:"confidence"`
	Metadata           map[string]any    `json:"metadata"`
	DataClassification string            `json:"data_classification"`
}

func New(cfg config.Config) Gateway {
	timeout := cfg.ModelGatewayTimeout
	if timeout <= 0 {
		timeout = 15 * time.Second
	}
	return Gateway{cfg: cfg, client: &http.Client{Timeout: timeout}}
}

func (g Gateway) Annotate(ctx context.Context, req Request) Response {
	started := time.Now()
	truncated := false
	if req.RedactionPassed && g.cfg.ModelGatewayMaxInputChars > 0 && utf8.RuneCountInString(req.RedactedText) > g.cfg.ModelGatewayMaxInputChars {
		req.RedactedText = truncateRunes(req.RedactedText, g.cfg.ModelGatewayMaxInputChars)
		truncated = true
	}
	base := Response{
		ProviderType:       firstNonEmpty(g.cfg.ModelGatewayProviderType, "llm"),
		ProviderName:       firstNonEmpty(g.cfg.ModelGatewayProviderName, "local_rules"),
		Mode:               firstNonEmpty(strings.ToLower(g.cfg.ModelGatewayMode), "local"),
		Region:             firstNonEmpty(g.cfg.ModelGatewayRegion, "CN"),
		Model:              firstNonEmpty(g.cfg.ModelGatewayModel, "lodia-rules-v1"),
		PromptVersion:      firstNonEmpty(g.cfg.ModelGatewayPromptVersion, focus.SchemaVersion),
		Status:             "completed",
		InputHash:          hashString(req.RedactedText),
		InputTokens:        estimateTokens(req.RedactedText),
		DataClassification: "CN-D2-redacted-task-text",
		Labels:             map[string]string{},
		Metadata:           map[string]any{"schema": SchemaVersion},
	}
	if truncated {
		base.Metadata["truncated"] = true
	}
	if strings.EqualFold(base.Mode, "disabled") || strings.EqualFold(base.Mode, "off") {
		base.Status = "skipped"
		base.ErrorCode = "model_gateway_disabled"
		base.LatencyMS = elapsedMillis(started)
		base.OutputHash = hashJSON(base.Metadata)
		return base
	}
	if !req.RedactionPassed {
		base.Status = "skipped"
		base.ErrorCode = "redaction_not_passed"
		base.LatencyMS = elapsedMillis(started)
		base.OutputHash = hashJSON(base.Metadata)
		return base
	}
	switch base.Mode {
	case "http":
		return g.annotateHTTP(ctx, req, base, started)
	default:
		return g.annotateLocal(req, base, started)
	}
}

func (g Gateway) Health(ctx context.Context) map[string]any {
	mode := firstNonEmpty(strings.ToLower(g.cfg.ModelGatewayMode), "local")
	endpointConfigured := strings.TrimSpace(g.cfg.ModelGatewayEndpoint) != ""
	providerNameConfigured := strings.TrimSpace(g.cfg.ModelGatewayProviderName) != ""
	modelConfigured := strings.TrimSpace(g.cfg.ModelGatewayModel) != ""
	production := g.cfg.ProductionProfile()
	out := map[string]any{
		"ok":                       true,
		"mode":                     mode,
		"provider_type":            firstNonEmpty(g.cfg.ModelGatewayProviderType, "llm"),
		"provider_name":            firstNonEmpty(g.cfg.ModelGatewayProviderName, "local_rules"),
		"region":                   firstNonEmpty(g.cfg.ModelGatewayRegion, "CN"),
		"model":                    firstNonEmpty(g.cfg.ModelGatewayModel, "lodia-rules-v1"),
		"prompt_version":           firstNonEmpty(g.cfg.ModelGatewayPromptVersion, focus.SchemaVersion),
		"external_call":            mode == "http",
		"redaction_first":          true,
		"endpoint_configured":      endpointConfigured,
		"provider_name_configured": providerNameConfigured,
		"model_configured":         modelConfigured,
		"production_profile":       production,
	}
	reasons := []string{}
	if mode == "disabled" || mode == "off" {
		reasons = append(reasons, "model_gateway_disabled")
	}
	if mode == "http" && !endpointConfigured {
		reasons = append(reasons, "model_gateway_endpoint_required")
	}
	if production && mode != "http" {
		reasons = append(reasons, "production_requires_http_model_gateway")
	}
	if production && strings.ToUpper(firstNonEmpty(g.cfg.ModelGatewayRegion, "CN")) != "CN" {
		reasons = append(reasons, "china_production_requires_cn_region")
	}
	if production && !providerNameConfigured {
		reasons = append(reasons, "provider_name_required")
	}
	if production && !modelConfigured {
		reasons = append(reasons, "model_name_required")
	}
	if len(reasons) > 0 {
		out["ok"] = false
		out["reasons"] = reasons
	}
	_ = ctx
	return out
}

func (g Gateway) annotateLocal(req Request, base Response, started time.Time) Response {
	wb := req.Workbench
	if len(wb.Task) == 0 {
		wb = focus.Extract(req.RedactedText)
	}
	base.Labels = map[string]string{
		"model_gateway":        "local_rules",
		"model_gateway_schema": SchemaVersion,
		"data_focus":           firstNonEmpty(req.DataFocus, "llm_long_horizon_task"),
		"long_horizon_gate":    wb.Quality.Gate,
		"long_horizon_tier":    wb.Quality.Tier,
	}
	base.QualityScore = wb.Quality.Score
	base.Confidence = confidence(wb.Quality.Score)
	base.OutputTokens = estimateTokens(strings.Join(wb.Evidence.Missing, " "))
	base.Metadata["missing_fields"] = wb.Evidence.Missing
	base.Metadata["source_chars"] = wb.Evidence.SourceChars
	base.Metadata["allowed_uses"] = req.AllowedUses
	base.Metadata["external_transfer"] = false
	base.LatencyMS = elapsedMillis(started)
	base.OutputHash = hashJSON(map[string]any{"labels": base.Labels, "metadata": base.Metadata, "quality_score": base.QualityScore})
	return base
}

func (g Gateway) annotateHTTP(ctx context.Context, req Request, base Response, started time.Time) Response {
	endpoint := strings.TrimSpace(g.cfg.ModelGatewayEndpoint)
	if endpoint == "" {
		base.Status = "failed"
		base.ErrorCode = "model_gateway_endpoint_required"
		base.LatencyMS = elapsedMillis(started)
		base.OutputHash = hashJSON(base.Metadata)
		return base
	}
	payload := map[string]any{
		"operation":        firstNonEmpty(req.Operation, OperationAnnotation),
		"schema_version":   SchemaVersion,
		"redacted_text":    req.RedactedText,
		"allowed_uses":     req.AllowedUses,
		"data_focus":       req.DataFocus,
		"workbench":        req.Workbench,
		"prompt_version":   base.PromptVersion,
		"data_class":       base.DataClassification,
		"redaction_passed": req.RedactionPassed,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return failed(base, started, "request_encode_failed")
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return failed(base, started, "request_create_failed")
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if key := strings.TrimSpace(g.cfg.ModelGatewayAPIKey); key != "" {
		httpReq.Header.Set("Authorization", "Bearer "+key)
	}
	resp, err := g.client.Do(httpReq)
	if err != nil {
		return failed(base, started, "request_failed")
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return failed(base, started, "response_read_failed")
	}
	if resp.StatusCode >= 300 {
		out := failed(base, started, "http_status_"+resp.Status)
		out.OutputHash = hashBytes(respBody)
		return out
	}
	var parsed struct {
		Labels       map[string]string `json:"labels"`
		QualityScore float64           `json:"quality_score"`
		Confidence   float64           `json:"confidence"`
		Model        string            `json:"model"`
		Metadata     map[string]any    `json:"metadata"`
	}
	if err := json.Unmarshal(respBody, &parsed); err != nil {
		return failed(base, started, "response_decode_failed")
	}
	if parsed.Labels != nil {
		base.Labels = parsed.Labels
	}
	if parsed.QualityScore > 0 {
		base.QualityScore = parsed.QualityScore
	}
	if parsed.Confidence > 0 {
		base.Confidence = parsed.Confidence
	}
	if parsed.Model != "" {
		base.Model = parsed.Model
	}
	for key, value := range parsed.Metadata {
		base.Metadata[key] = value
	}
	base.Metadata["external_transfer"] = true
	base.LatencyMS = elapsedMillis(started)
	base.OutputHash = hashBytes(respBody)
	return base
}

func failed(base Response, started time.Time, code string) Response {
	base.Status = "failed"
	base.ErrorCode = code
	base.LatencyMS = elapsedMillis(started)
	base.OutputHash = hashJSON(base.Metadata)
	return base
}

func (r Response) Err() error {
	if r.Status == "failed" {
		return errors.New(r.ErrorCode)
	}
	return nil
}

func hashString(value string) string {
	return hashBytes([]byte(value))
}

func hashBytes(value []byte) string {
	sum := sha256.Sum256(value)
	return hex.EncodeToString(sum[:])
}

func hashJSON(value any) string {
	body, _ := json.Marshal(value)
	return hashBytes(body)
}

func estimateTokens(value string) int {
	runes := utf8.RuneCountInString(value)
	if runes == 0 {
		return 0
	}
	return runes/4 + 1
}

func elapsedMillis(started time.Time) int64 {
	ms := time.Since(started).Milliseconds()
	if ms < 0 {
		return 0
	}
	return ms
}

func confidence(score float64) float64 {
	switch {
	case score >= 0.78:
		return 0.82
	case score >= 0.5:
		return 0.68
	default:
		return 0.52
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func truncateRunes(value string, limit int) string {
	if limit <= 0 || utf8.RuneCountInString(value) <= limit {
		return value
	}
	runes := []rune(value)
	return string(runes[:limit])
}
