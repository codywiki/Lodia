package auth

import (
	"net/http"
	"strings"

	"github.com/codywiki/lodia/apps/api-go/internal/config"
)

type Context struct {
	Subject string
	Roles   map[string]bool
	Enabled bool
}

func FromRequest(r *http.Request, cfg config.Config) Context {
	if !cfg.AuthEnabled() {
		if strings.EqualFold(cfg.Env, "production") {
			return Context{Enabled: true, Roles: map[string]bool{}}
		}
		return Context{Subject: "demo", Roles: map[string]bool{"admin": true, "reviewer": true, "contributor": true}}
	}
	token := BearerToken(r.Header.Get("Authorization"))
	switch token {
	case cfg.AdminToken:
		return Context{Subject: "admin", Enabled: true, Roles: map[string]bool{"admin": true, "reviewer": true, "contributor": true}}
	case cfg.ReviewerToken:
		return Context{Subject: "reviewer", Enabled: true, Roles: map[string]bool{"reviewer": true}}
	case cfg.ContributorToken:
		return Context{Subject: "contributor", Enabled: true, Roles: map[string]bool{"contributor": true}}
	default:
		return Context{Enabled: true, Roles: map[string]bool{}}
	}
}

func (c Context) HasAny(roles ...string) bool {
	for _, role := range roles {
		if c.Roles[role] {
			return true
		}
	}
	return false
}

func BearerToken(value string) string {
	value = strings.TrimSpace(value)
	if strings.HasPrefix(strings.ToLower(value), "bearer ") {
		return strings.TrimSpace(value[7:])
	}
	return value
}
