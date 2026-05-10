package config

import (
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Env                     string
	Deployment              string
	DataFocus               string
	HTTPAddr                string
	MySQLDSN                string
	RedisURL                string
	AsyncProcessing         bool
	WorkerQueue             string
	MaxRequestBytes         int64
	RateLimitEnabled        bool
	RateLimitRequests       int
	RateLimitWindow         time.Duration
	TrustProxyHeaders       bool
	AccessLogEnabled        bool
	DatasetMaxCases         int
	RawObjectTTL            time.Duration
	PurgeRawAfterProcessing bool
	AllowedOrigins          []string
	AdminToken              string
	ReviewerToken           string
	ContributorToken        string
	PasswordPepper          string

	ModelGatewayMode          string
	ModelGatewayProviderType  string
	ModelGatewayProviderName  string
	ModelGatewayRegion        string
	ModelGatewayEndpoint      string
	ModelGatewayAPIKey        string
	ModelGatewayModel         string
	ModelGatewayPromptVersion string
	ModelGatewayTimeout       time.Duration
	ModelGatewayMaxInputChars int

	ObjectBackend         string
	ObjectDir             string
	OSSEndpoint           string
	OSSBucket             string
	OSSAccessKey          string
	OSSSecretKey          string
	OSSPrefix             string
	OSSSTSEnabled         bool
	OSSSTSRoleARN         string
	OSSSTSEndpoint        string
	OSSSTSSessionName     string
	OSSSTSDurationSeconds int
}

func FromEnv() Config {
	return Config{
		Env:                     env("LODIA_ENV", "development"),
		Deployment:              env("LODIA_DEPLOYMENT_PROFILE", "development"),
		DataFocus:               env("LODIA_DATA_FOCUS", "llm_long_horizon_task"),
		HTTPAddr:                env("LODIA_HTTP_ADDR", ":8080"),
		MySQLDSN:                env("MYSQL_DSN", "lodia:lodia_dev_only@tcp(127.0.0.1:3306)/lodia?parseTime=true&charset=utf8mb4&loc=UTC"),
		RedisURL:                env("REDIS_URL", "redis://127.0.0.1:6379/0"),
		AsyncProcessing:         boolEnv("LODIA_ASYNC_PROCESSING", true),
		WorkerQueue:             env("LODIA_WORKER_QUEUE", "ingestion"),
		MaxRequestBytes:         int64Env("LODIA_MAX_REQUEST_BODY_BYTES", 1_048_576),
		RateLimitEnabled:        boolEnv("LODIA_RATE_LIMIT_ENABLED", false),
		RateLimitRequests:       intEnv("LODIA_RATE_LIMIT_REQUESTS", 600),
		RateLimitWindow:         time.Duration(intEnv("LODIA_RATE_LIMIT_WINDOW_SECONDS", 60)) * time.Second,
		TrustProxyHeaders:       boolEnv("LODIA_TRUST_PROXY_HEADERS", false),
		AccessLogEnabled:        boolEnv("LODIA_ACCESS_LOG_ENABLED", true),
		DatasetMaxCases:         intEnv("LODIA_DATASET_MAX_CASES", 5000),
		RawObjectTTL:            time.Duration(intEnv("LODIA_RAW_OBJECT_TTL_HOURS", 24)) * time.Hour,
		PurgeRawAfterProcessing: boolEnv("LODIA_PURGE_RAW_AFTER_PROCESSING", true),
		AllowedOrigins:          splitEnv("LODIA_ALLOWED_ORIGINS"),
		AdminToken:              os.Getenv("LODIA_ADMIN_TOKEN"),
		ReviewerToken:           os.Getenv("LODIA_REVIEWER_TOKEN"),
		ContributorToken:        os.Getenv("LODIA_CONTRIBUTOR_TOKEN"),
		PasswordPepper:          os.Getenv("LODIA_PASSWORD_PEPPER"),
		ModelGatewayMode:        strings.ToLower(env("LODIA_MODEL_GATEWAY_MODE", "local")),
		ModelGatewayProviderType: env(
			"LODIA_MODEL_GATEWAY_PROVIDER_TYPE",
			"llm",
		),
		ModelGatewayProviderName:  env("LODIA_MODEL_GATEWAY_PROVIDER_NAME", "local_rules"),
		ModelGatewayRegion:        env("LODIA_MODEL_GATEWAY_REGION", "CN"),
		ModelGatewayEndpoint:      os.Getenv("LODIA_MODEL_GATEWAY_ENDPOINT"),
		ModelGatewayAPIKey:        os.Getenv("LODIA_MODEL_GATEWAY_API_KEY"),
		ModelGatewayModel:         env("LODIA_MODEL_GATEWAY_MODEL", "lodia-rules-v1"),
		ModelGatewayPromptVersion: env("LODIA_MODEL_GATEWAY_PROMPT_VERSION", "long_horizon_task.v1"),
		ModelGatewayTimeout:       time.Duration(intEnv("LODIA_MODEL_GATEWAY_TIMEOUT_SECONDS", 15)) * time.Second,
		ModelGatewayMaxInputChars: intEnv("LODIA_MODEL_GATEWAY_MAX_INPUT_CHARS", 8000),
		ObjectBackend:             env("LODIA_OBJECT_STORAGE_BACKEND", "local"),
		ObjectDir:                 env("LODIA_OBJECT_STORAGE_DIR", "storage/dev/objects"),
		OSSEndpoint:               os.Getenv("LODIA_OSS_ENDPOINT"),
		OSSBucket:                 os.Getenv("LODIA_OSS_BUCKET"),
		OSSAccessKey:              first(os.Getenv("LODIA_OSS_ACCESS_KEY_ID"), os.Getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")),
		OSSSecretKey:              first(os.Getenv("LODIA_OSS_ACCESS_KEY_SECRET"), os.Getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")),
		OSSPrefix:                 env("LODIA_OSS_PREFIX", "lodia"),
		OSSSTSEnabled:             boolEnv("LODIA_OBJECT_STORAGE_STS_ENABLED", false),
		OSSSTSRoleARN:             os.Getenv("LODIA_OSS_STS_ROLE_ARN"),
		OSSSTSEndpoint:            env("LODIA_OSS_STS_ENDPOINT_URL", "https://sts.aliyuncs.com"),
		OSSSTSSessionName:         env("LODIA_OSS_STS_SESSION_NAME", "lodia-upload"),
		OSSSTSDurationSeconds:     intEnv("LODIA_OSS_STS_DURATION_SECONDS", 900),
	}
}

func (c Config) AuthEnabled() bool {
	return c.AdminToken != "" || c.ReviewerToken != "" || c.ContributorToken != ""
}

func (c Config) ProductionProfile() bool {
	switch strings.ToLower(strings.TrimSpace(c.Deployment)) {
	case "production", "china_independent", "china-production", "cn-production":
		return true
	default:
		return false
	}
}

func env(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func first(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func splitEnv(key string) []string {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return nil
	}
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		if clean := strings.TrimSpace(part); clean != "" {
			out = append(out, clean)
		}
	}
	return out
}

func boolEnv(key string, fallback bool) bool {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback
	}
	return raw == "1" || strings.EqualFold(raw, "true") || strings.EqualFold(raw, "yes")
}

func intEnv(key string, fallback int) int {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return value
}

func int64Env(key string, fallback int64) int64 {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return fallback
	}
	return value
}
