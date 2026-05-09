package httpapi

import (
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/codywiki/lodia/apps/api-go/internal/store"
)

func (s *Server) createUser(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "admin")
	if !ok {
		return
	}
	var req struct {
		Email       string `json:"email"`
		DisplayName string `json:"display_name"`
		Role        string `json:"role"`
		Password    string `json:"password"`
		Status      string `json:"status"`
	}
	if !decodeOr400(w, r, &req) {
		return
	}
	if strings.TrimSpace(req.Email) == "" || strings.TrimSpace(req.Password) == "" {
		writeError(w, http.StatusBadRequest, "email_and_password_required")
		return
	}
	user := store.User{Email: req.Email, DisplayName: req.DisplayName, Role: req.Role, Status: firstNonEmpty(req.Status, "active")}
	if err := s.db.CreateUser(r.Context(), &user, req.Password, s.cfg.PasswordPepper); err != nil {
		writeError(w, http.StatusConflict, "user_create_failed")
		return
	}
	_ = s.db.Audit(r.Context(), actor.Subject, "user.created", "user", user.ID, map[string]any{"email_domain": emailDomain(user.Email), "role": user.Role})
	writeJSON(w, http.StatusOK, store.UserPayload(user))
}

func (s *Server) listUsers(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.require(w, r, "admin", "reviewer"); !ok {
		return
	}
	users, err := s.db.ListUsers(r.Context(), queryLimit(r, 20))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	items := make([]map[string]any, 0, len(users))
	for _, user := range users {
		items = append(items, store.UserPayload(user))
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": items})
}

func (s *Server) issueUserToken(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "admin")
	if !ok {
		return
	}
	var req struct {
		TTLHours int `json:"ttl_hours"`
	}
	_ = readJSON(r, &req)
	ttl := time.Duration(firstPositive(req.TTLHours, 24*30)) * time.Hour
	token, rawToken, err := s.db.IssueAuthToken(r.Context(), r.PathValue("id"), actor.Subject, ttl)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	resp := store.AuthTokenPayload(token)
	resp["token"] = rawToken
	_ = s.db.Audit(r.Context(), actor.Subject, "auth_token.issued", "auth_token", token.ID, map[string]any{"user_id": token.UserID, "role": token.Role, "token_suffix": token.TokenSuffix})
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) revokeAuthToken(w http.ResponseWriter, r *http.Request) {
	actor, ok := s.require(w, r, "admin")
	if !ok {
		return
	}
	token, err := s.db.RevokeAuthToken(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	_ = s.db.Audit(r.Context(), actor.Subject, "auth_token.revoked", "auth_token", token.ID, map[string]any{"user_id": token.UserID})
	writeJSON(w, http.StatusOK, store.AuthTokenPayload(token))
}

func loginError(w http.ResponseWriter, err error) bool {
	if errors.Is(err, store.ErrInvalidCredential) {
		writeError(w, http.StatusUnauthorized, "invalid_credentials")
		return true
	}
	return false
}
