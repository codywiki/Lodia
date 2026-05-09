package httpapi

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/codywiki/lodia/apps/api-go/internal/annotation"
	"github.com/codywiki/lodia/apps/api-go/internal/config"
	"github.com/codywiki/lodia/apps/api-go/internal/focus"
	"github.com/codywiki/lodia/apps/api-go/internal/objectstore"
	"github.com/codywiki/lodia/apps/api-go/internal/redaction"
	"github.com/codywiki/lodia/apps/api-go/internal/store"
)

type Processor struct {
	cfg     config.Config
	db      *store.DB
	objects objectstore.Store
}

func NewProcessor(cfg config.Config, db *store.DB, objects objectstore.Store) Processor {
	return Processor{cfg: cfg, db: db, objects: objects}
}

func (p Processor) Preview(text string, allowedUses []string) annotation.Preview {
	return annotation.BuildPreview(store.NewID("preview"), text, allowedUses, p.cfg.DataFocus)
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
	canonicalHash := redaction.CanonicalHash(preview.Redaction.RedactedText)
	if existing, err := p.db.FindCaseByCanonicalHash(ctx, canonicalHash); err == nil {
		_ = p.db.UpdateSubmissionStatus(ctx, sub.ID, "duplicate", existing.ID)
		p.purgeRaw(ctx, sub)
		_ = p.db.Audit(ctx, sub.OwnerID, "submission.duplicate_detected", "submission", sub.ID, map[string]any{"duplicate_of_case_id": existing.ID})
		return existing, nil
	}
	workbench := focus.Extract(preview.Redaction.RedactedText)
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
