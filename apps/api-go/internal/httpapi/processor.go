package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/codywiki/lodia/apps/api-go/internal/annotation"
	"github.com/codywiki/lodia/apps/api-go/internal/config"
	"github.com/codywiki/lodia/apps/api-go/internal/focus"
	"github.com/codywiki/lodia/apps/api-go/internal/modelgateway"
	"github.com/codywiki/lodia/apps/api-go/internal/objectstore"
	"github.com/codywiki/lodia/apps/api-go/internal/redaction"
	"github.com/codywiki/lodia/apps/api-go/internal/store"
)

type Processor struct {
	cfg     config.Config
	db      *store.DB
	objects objectstore.Store
	gateway modelgateway.Gateway
}

func NewProcessor(cfg config.Config, db *store.DB, objects objectstore.Store) Processor {
	return Processor{cfg: cfg, db: db, objects: objects, gateway: modelgateway.New(cfg)}
}

func (p Processor) Preview(ctx context.Context, text string, allowedUses []string) annotation.Preview {
	preview := annotation.BuildPreview(store.NewID("preview"), text, allowedUses, p.cfg.DataFocus)
	workbench := focus.Extract(preview.Redaction.RedactedText)
	p.applyModelGateway(ctx, "preview", preview.CaseID, allowedUses, &preview, workbench)
	return preview
}

func (p Processor) ProcessSubmission(ctx context.Context, submissionID string) (store.Case, error) {
	sub, err := p.db.GetSubmission(ctx, submissionID)
	if err != nil {
		return store.Case{}, err
	}
	if sub.RawDeletedAt != nil {
		return store.Case{}, errors.New("raw_object_already_deleted")
	}
	text, err := p.objects.GetText(ctx, sub.RawObjectURI)
	if err != nil {
		_ = p.db.UpdateSubmissionStatus(ctx, sub.ID, "failed", "")
		return store.Case{}, err
	}
	caseID := store.NewID("case")
	preview := annotation.BuildPreview(caseID, text, sub.AllowedUses, p.cfg.DataFocus)
	workbench := focus.Extract(preview.Redaction.RedactedText)
	p.applyModelGateway(ctx, "case", caseID, sub.AllowedUses, &preview, workbench)
	canonicalHash := redaction.CanonicalHash(preview.Redaction.RedactedText)
	if existing, err := p.db.FindCaseByCanonicalHash(ctx, canonicalHash); err == nil {
		_ = p.db.UpdateSubmissionStatus(ctx, sub.ID, "duplicate", existing.ID)
		p.purgeRaw(ctx, sub)
		_ = p.db.Audit(ctx, sub.OwnerID, "submission.duplicate_detected", "submission", sub.ID, map[string]any{"duplicate_of_case_id": existing.ID})
		return existing, nil
	}
	c := store.Case{
		ID:              caseID,
		SubmissionID:    sub.ID,
		OwnerID:         sub.OwnerID,
		Status:          caseStatus(preview.QualityGate),
		RedactedText:    preview.Redaction.RedactedText,
		RawHash:         sub.RawHash,
		CanonicalHash:   canonicalHash,
		DRL:             preview.QualityGate.DRL,
		CommercialReady: preview.QualityGate.CommercialReady,
		RedactionJSON:   annotation.Marshal(preview.Redaction),
		AnnotationJSON:  annotation.Marshal(preview.Annotation),
		QualityGateJSON: annotation.Marshal(preview.QualityGate),
		LongHorizonJSON: annotation.Marshal(workbench),
	}
	if err := p.db.CreateCase(ctx, &c); err != nil {
		if existing, findErr := p.db.FindCaseByCanonicalHash(ctx, canonicalHash); findErr == nil {
			_ = p.db.UpdateSubmissionStatus(ctx, sub.ID, "duplicate", existing.ID)
			p.purgeRaw(ctx, sub)
			return existing, nil
		}
		_ = p.db.UpdateSubmissionStatus(ctx, sub.ID, "failed", "")
		return store.Case{}, err
	}
	_ = p.db.UpdateSubmissionStatus(ctx, sub.ID, "processed", "")
	p.purgeRaw(ctx, sub)
	_ = p.db.Audit(ctx, sub.OwnerID, "case.processed", "case", c.ID, map[string]any{"submission_id": sub.ID, "drl": c.DRL, "commercial_ready": c.CommercialReady})
	return p.db.GetCase(ctx, c.ID)
}

func (p Processor) ModelGatewayHealth(ctx context.Context) map[string]any {
	return p.gateway.Health(ctx)
}

func (p Processor) applyModelGateway(ctx context.Context, entityType string, entityID string, allowedUses []string, preview *annotation.Preview, workbench focus.Workbench) {
	resp := p.gateway.Annotate(ctx, modelgateway.Request{
		Operation:       modelgateway.OperationAnnotation,
		EntityType:      entityType,
		EntityID:        entityID,
		RedactedText:    preview.Redaction.RedactedText,
		AllowedUses:     allowedUses,
		DataFocus:       p.cfg.DataFocus,
		Workbench:       workbench,
		RedactionPassed: preview.Redaction.Passed,
	})
	if preview.Annotation.Labels == nil {
		preview.Annotation.Labels = map[string]string{}
	}
	preview.Annotation.Labels["model_gateway_provider"] = resp.ProviderName
	preview.Annotation.Labels["model_gateway_mode"] = resp.Mode
	preview.Annotation.Labels["model_gateway_status"] = resp.Status
	preview.Annotation.Labels["model_gateway_prompt_version"] = resp.PromptVersion
	if resp.Model != "" {
		preview.Annotation.Labels["model_gateway_model"] = resp.Model
	}
	for key, value := range resp.Labels {
		clean := strings.TrimSpace(key)
		if clean == "" {
			continue
		}
		preview.Annotation.Labels["model_gateway_label_"+clean] = value
	}
	preview.QualityGate.GateResults["model_gateway"] = resp.Status
	if resp.Status == "failed" {
		preview.QualityGate.RequiredActions = appendMissingString(preview.QualityGate.RequiredActions, "model_gateway_retry")
		preview.QualityGate.CommercialReady = false
	}
	if err := p.recordModelGatewayCall(ctx, entityType, entityID, resp); err != nil {
		preview.QualityGate.GateResults["model_gateway_audit"] = "failed"
		preview.QualityGate.RequiredActions = appendMissingString(preview.QualityGate.RequiredActions, "model_gateway_audit_retry")
		preview.QualityGate.CommercialReady = false
	} else if p.db != nil {
		preview.QualityGate.GateResults["model_gateway_audit"] = "recorded"
	}
}

func (p Processor) recordModelGatewayCall(ctx context.Context, entityType string, entityID string, resp modelgateway.Response) error {
	if p.db == nil {
		return nil
	}
	metadata, err := json.Marshal(resp.Metadata)
	if err != nil {
		return err
	}
	if string(metadata) == "null" {
		metadata = []byte("{}")
	}
	return p.db.CreateVendorProcessingRecord(ctx, &store.VendorProcessingRecord{
		ProviderType:       resp.ProviderType,
		ProviderName:       resp.ProviderName,
		Operation:          modelgateway.OperationAnnotation,
		EntityType:         entityType,
		EntityID:           entityID,
		Status:             resp.Status,
		Region:             resp.Region,
		DataClassification: resp.DataClassification,
		InputHash:          resp.InputHash,
		OutputHash:         resp.OutputHash,
		PromptVersion:      resp.PromptVersion,
		ModelName:          resp.Model,
		LatencyMS:          resp.LatencyMS,
		InputTokens:        resp.InputTokens,
		OutputTokens:       resp.OutputTokens,
		CostMicros:         resp.CostMicros,
		ErrorCode:          resp.ErrorCode,
		MetadataJSON:       string(metadata),
	})
}

func (p Processor) purgeRaw(ctx context.Context, sub store.Submission) {
	if !p.cfg.PurgeRawAfterProcessing || sub.RawObjectURI == "" {
		return
	}
	if err := p.objects.Delete(ctx, sub.RawObjectURI); err == nil {
		_ = p.db.MarkSubmissionRawDeleted(ctx, sub.ID)
	}
}

func caseStatus(gate annotation.QualityGate) string {
	if gate.DRL == "DRL0" || gate.DRL == "DRL1" {
		return "needs_review"
	}
	return "review_ready"
}

func rawExpiry(ttl time.Duration) *time.Time {
	if ttl <= 0 {
		return nil
	}
	value := time.Now().UTC().Add(ttl).Truncate(time.Microsecond)
	return &value
}

func objectKey(parts ...string) string {
	return fmt.Sprintf("%s/%s", time.Now().UTC().Format("2006/01/02"), joinPath(parts...))
}

func joinPath(parts ...string) string {
	out := ""
	for _, part := range parts {
		if part == "" {
			continue
		}
		if out == "" {
			out = part
		} else {
			out += "/" + part
		}
	}
	return out
}

func appendMissingString(values []string, next string) []string {
	for _, value := range values {
		if value == next {
			return values
		}
	}
	return append(values, next)
}
