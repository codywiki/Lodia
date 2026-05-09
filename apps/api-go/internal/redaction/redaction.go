package redaction

import (
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"sort"
	"strings"
)

type Finding struct {
	Type        string  `json:"type"`
	Count       int     `json:"count"`
	Severity    string  `json:"severity"`
	Replacement string  `json:"replacement"`
	Confidence  float64 `json:"confidence"`
}

type Result struct {
	RedactedText     string    `json:"redacted_text"`
	Findings         []Finding `json:"findings"`
	PrivacyRiskScore float64   `json:"privacy_risk_score"`
	Passed           bool      `json:"passed"`
}

type pattern struct {
	kind        string
	severity    string
	replacement string
	confidence  float64
	re          *regexp.Regexp
}

var patterns = []pattern{
	{kind: "jwt", severity: "critical", replacement: "[REDACTED_JWT]", confidence: 0.98, re: regexp.MustCompile(`\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b`)},
	{kind: "access_key", severity: "critical", replacement: "[REDACTED_ACCESS_KEY]", confidence: 0.96, re: regexp.MustCompile(`\b(?:AKIA|ASIA|LTAI)[A-Za-z0-9]{12,32}\b`)},
	{kind: "secret_assignment", severity: "critical", replacement: "$1=[REDACTED_SECRET]", confidence: 0.92, re: regexp.MustCompile(`(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)\s*[:=]\s*['"]?[^'"\s,;]{6,}`)},
	{kind: "email", severity: "high", replacement: "[REDACTED_EMAIL]", confidence: 0.95, re: regexp.MustCompile(`\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b`)},
	{kind: "cn_phone", severity: "high", replacement: "[REDACTED_PHONE]", confidence: 0.95, re: regexp.MustCompile(`(?:(?:\+?86)[-\s]?)?1[3-9]\d{9}\b`)},
	{kind: "cn_id_card", severity: "high", replacement: "[REDACTED_ID_CARD]", confidence: 0.86, re: regexp.MustCompile(`\b[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b`)},
	{kind: "bank_card", severity: "high", replacement: "[REDACTED_BANK_CARD]", confidence: 0.78, re: regexp.MustCompile(`\b(?:\d[ -]*?){16,19}\b`)},
	{kind: "private_ipv4", severity: "medium", replacement: "[REDACTED_PRIVATE_IP]", confidence: 0.82, re: regexp.MustCompile(`\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b192\.168\.\d{1,3}\.\d{1,3}\b|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b`)},
	{kind: "internal_url", severity: "medium", replacement: "[REDACTED_INTERNAL_URL]", confidence: 0.8, re: regexp.MustCompile(`(?i)\bhttps?://(?:localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|[A-Za-z0-9_.-]+\.local)(?:/[^\s]*)?`)},
}

func Redact(text string) Result {
	redacted := text
	findingsByType := map[string]Finding{}
	for _, p := range patterns {
		matches := p.re.FindAllString(redacted, -1)
		if len(matches) == 0 {
			continue
		}
		f := findingsByType[p.kind]
		if f.Type == "" {
			f = Finding{Type: p.kind, Severity: p.severity, Replacement: normalizeReplacement(p.replacement), Confidence: p.confidence}
		}
		f.Count += len(matches)
		findingsByType[p.kind] = f
		redacted = p.re.ReplaceAllString(redacted, p.replacement)
	}
	findings := make([]Finding, 0, len(findingsByType))
	for _, finding := range findingsByType {
		findings = append(findings, finding)
	}
	sort.Slice(findings, func(i, j int) bool {
		if severityWeight(findings[i].Severity) == severityWeight(findings[j].Severity) {
			return findings[i].Type < findings[j].Type
		}
		return severityWeight(findings[i].Severity) > severityWeight(findings[j].Severity)
	})
	score := riskScore(findings)
	return Result{
		RedactedText:     redacted,
		Findings:         findings,
		PrivacyRiskScore: score,
		Passed:           score < 0.85 && residualRisk(redacted) < 0.85,
	}
}

func HashRaw(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

func CanonicalHash(text string) string {
	canonical := strings.ToLower(strings.Join(strings.Fields(text), " "))
	sum := sha256.Sum256([]byte(canonical))
	return hex.EncodeToString(sum[:])
}

func riskScore(findings []Finding) float64 {
	var score float64
	for _, finding := range findings {
		score += float64(finding.Count) * severityContribution(finding.Severity) * finding.Confidence
	}
	if score > 1 {
		return 1
	}
	return score
}

func residualRisk(text string) float64 {
	var residual []Finding
	for _, p := range patterns {
		if p.re.MatchString(text) {
			residual = append(residual, Finding{Type: p.kind, Count: 1, Severity: p.severity, Confidence: p.confidence})
		}
	}
	return riskScore(residual)
}

func severityContribution(severity string) float64 {
	switch severity {
	case "critical":
		return 0.55
	case "high":
		return 0.35
	case "medium":
		return 0.18
	default:
		return 0.08
	}
}

func severityWeight(severity string) int {
	switch severity {
	case "critical":
		return 4
	case "high":
		return 3
	case "medium":
		return 2
	default:
		return 1
	}
}

func normalizeReplacement(value string) string {
	if strings.HasPrefix(value, "$1=") {
		return "[REDACTED_SECRET]"
	}
	return value
}
