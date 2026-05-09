package focus

import (
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

const SchemaVersion = "long_horizon_task.v1"

var FieldNames = []string{
	"objective",
	"context",
	"constraints",
	"steps",
	"tool_results",
	"failures",
	"corrections",
	"acceptance",
	"reusable_rules",
}

var RequiredFields = []string{"objective", "steps", "acceptance", "reusable_rules"}

type Task map[string][]string

type Evidence struct {
	Missing       []string `json:"missing"`
	RefinedFields []string `json:"refined_fields,omitempty"`
	SourceChars   int      `json:"source_chars"`
}

type Quality struct {
	Score     float64 `json:"score"`
	Tier      string  `json:"tier"`
	Gate      string  `json:"gate"`
	Refined   bool    `json:"refined"`
	RefinedBy string  `json:"refined_by"`
	RefinedAt string  `json:"refined_at"`
}

type Workbench struct {
	Task     Task     `json:"task"`
	Evidence Evidence `json:"evidence"`
	Quality  Quality  `json:"quality"`
}

type FieldQuality struct {
	Score                 float64  `json:"score"`
	Tier                  string   `json:"tier"`
	Passed                bool     `json:"passed"`
	FilledFields          []string `json:"filled_fields"`
	Missing               []string `json:"missing"`
	RequiredMissing       []string `json:"required_missing"`
	SourceEvidenceTooThin bool     `json:"source_evidence_too_thin"`
}

type signalRule struct {
	field    string
	patterns []string
}

var signalRules = []signalRule{
	{field: "objective", patterns: []string{"目标", "目的", "需要", "希望", "objective", "goal", "target", "task"}},
	{field: "context", patterns: []string{"背景", "上下文", "现状", "环境", "context", "background", "scenario"}},
	{field: "constraints", patterns: []string{"约束", "限制", "不能", "必须", "只允许", "constraint", "limit", "must", "cannot"}},
	{field: "steps", patterns: []string{"过程", "步骤", "执行", "查看", "定位", "修复", "重跑", "step", "run", "debug", "fix", "deploy"}},
	{field: "tool_results", patterns: []string{"日志", "结果", "输出", "报错", "返回", "通过", "log", "output", "result", "status"}},
	{field: "failures", patterns: []string{"失败", "错误", "异常", "超时", "502", "500", "failed", "error", "timeout"}},
	{field: "corrections", patterns: []string{"调整", "改为", "修正", "回滚", "补充", "retry", "rerun", "correct", "change"}},
	{field: "acceptance", patterns: []string{"验收", "通过", "完成", "ready", "acceptance", "verify", "success"}},
	{field: "reusable_rules", patterns: []string{"规则", "经验", "复用", "沉淀", "教训", "rule", "lesson", "pattern", "playbook"}},
}

var sentenceSplit = regexp.MustCompile(`[。\n；;.!?]+`)

func Extract(text string) Workbench {
	task := EmptyTask()
	for _, sentence := range sentenceSplit.Split(text, -1) {
		clean := normalizeLine(sentence)
		if clean == "" {
			continue
		}
		lower := strings.ToLower(clean)
		for _, rule := range signalRules {
			if containsAny(lower, rule.patterns) {
				task[rule.field] = appendUnique(task[rule.field], clean)
			}
		}
	}
	if len(task["objective"]) == 0 && strings.TrimSpace(text) != "" {
		task["objective"] = []string{truncateRunes(strings.TrimSpace(text), 220)}
	}
	quality := Evaluate(task, utf8.RuneCountInString(text))
	return Workbench{
		Task: task,
		Evidence: Evidence{
			Missing:     quality.Missing,
			SourceChars: utf8.RuneCountInString(text),
		},
		Quality: Quality{
			Score: quality.Score,
			Tier:  quality.Tier,
			Gate:  gateForQuality(quality),
		},
	}
}

func EmptyTask() Task {
	task := Task{}
	for _, field := range FieldNames {
		task[field] = []string{}
	}
	return task
}

func NormalizeTask(input Task) Task {
	out := EmptyTask()
	for _, field := range FieldNames {
		for _, value := range input[field] {
			clean := normalizeLine(value)
			if clean != "" {
				out[field] = appendUnique(out[field], clean)
			}
		}
	}
	return out
}

func Evaluate(task Task, sourceChars int) FieldQuality {
	task = NormalizeTask(task)
	filled := make([]string, 0, len(FieldNames))
	missing := make([]string, 0)
	requiredMissing := make([]string, 0)
	for _, field := range FieldNames {
		if len(task[field]) > 0 {
			filled = append(filled, field)
			continue
		}
		missing = append(missing, field)
		if isRequired(field) {
			requiredMissing = append(requiredMissing, field)
		}
	}
	sort.Strings(filled)
	score := float64(len(filled)) / float64(len(FieldNames))
	if sourceChars >= 120 {
		score += 0.08
	}
	if sourceChars >= 600 {
		score += 0.08
	}
	if len(requiredMissing) > 0 {
		score -= float64(len(requiredMissing)) * 0.08
	}
	if score < 0 {
		score = 0
	}
	if score > 1 {
		score = 1
	}
	tooThin := sourceChars < 120
	passed := len(requiredMissing) == 0 && len(filled) >= 5 && !tooThin
	return FieldQuality{
		Score:                 round2(score),
		Tier:                  tier(score),
		Passed:                passed,
		FilledFields:          filled,
		Missing:               missing,
		RequiredMissing:       requiredMissing,
		SourceEvidenceTooThin: tooThin,
	}
}

func BuildWorkbench(task Task, sourceChars int, refinedBy string, refined bool) Workbench {
	task = NormalizeTask(task)
	quality := Evaluate(task, sourceChars)
	refinedAt := ""
	if refined {
		refinedAt = time.Now().UTC().Format(time.RFC3339)
	}
	return Workbench{
		Task: task,
		Evidence: Evidence{
			Missing:       quality.Missing,
			RefinedFields: quality.FilledFields,
			SourceChars:   sourceChars,
		},
		Quality: Quality{
			Score:     quality.Score,
			Tier:      quality.Tier,
			Gate:      gateForQuality(quality),
			Refined:   refined,
			RefinedBy: refinedBy,
			RefinedAt: refinedAt,
		},
	}
}

func containsAny(value string, needles []string) bool {
	for _, needle := range needles {
		if strings.Contains(value, strings.ToLower(needle)) {
			return true
		}
	}
	return false
}

func appendUnique(values []string, next string) []string {
	for _, value := range values {
		if value == next {
			return values
		}
	}
	return append(values, next)
}

func normalizeLine(value string) string {
	value = strings.TrimSpace(value)
	value = strings.Trim(value, "-*# \t\r\n")
	return strings.Join(strings.Fields(value), " ")
}

func truncateRunes(value string, limit int) string {
	if utf8.RuneCountInString(value) <= limit {
		return value
	}
	runes := []rune(value)
	return string(runes[:limit])
}

func isRequired(field string) bool {
	for _, required := range RequiredFields {
		if required == field {
			return true
		}
	}
	return false
}

func tier(score float64) string {
	switch {
	case score >= 0.82:
		return "A"
	case score >= 0.68:
		return "B"
	case score >= 0.5:
		return "C"
	default:
		return "D"
	}
}

func gateForQuality(quality FieldQuality) string {
	if quality.Passed {
		return "passed"
	}
	if quality.SourceEvidenceTooThin {
		return "needs_source_evidence"
	}
	return "needs_field_refinement"
}

func round2(value float64) float64 {
	return float64(int(value*100+0.5)) / 100
}
