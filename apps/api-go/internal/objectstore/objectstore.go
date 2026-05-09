package objectstore

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha1"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"

	"github.com/codywiki/lodia/apps/api-go/internal/config"
)

type Store interface {
	Put(ctx context.Context, key string, body []byte, contentType string) (string, error)
	PutText(ctx context.Context, key string, text string, contentType string) (string, error)
	Get(ctx context.Context, uri string) ([]byte, error)
	GetText(ctx context.Context, uri string) (string, error)
	Delete(ctx context.Context, uri string) error
	Health(ctx context.Context) map[string]any
}

func New(cfg config.Config) (Store, error) {
	if strings.EqualFold(cfg.ObjectBackend, "oss") {
		if cfg.OSSEndpoint == "" || cfg.OSSBucket == "" || cfg.OSSAccessKey == "" || cfg.OSSSecretKey == "" {
			return nil, errors.New("oss_config_required")
		}
		return &OSS{endpoint: strings.TrimRight(cfg.OSSEndpoint, "/"), bucket: cfg.OSSBucket, accessKey: cfg.OSSAccessKey, secretKey: cfg.OSSSecretKey, prefix: cleanPrefix(cfg.OSSPrefix), client: &http.Client{Timeout: 30 * time.Second}}, nil
	}
	if err := os.MkdirAll(cfg.ObjectDir, 0o700); err != nil {
		return nil, err
	}
	return &Local{root: cfg.ObjectDir}, nil
}

type Local struct {
	root string
}

func (s *Local) PutText(ctx context.Context, key string, text string, contentType string) (string, error) {
	return s.Put(ctx, key, []byte(text), contentType)
}

func (s *Local) Put(ctx context.Context, key string, body []byte, contentType string) (string, error) {
	_ = ctx
	filePath, err := s.safePath(key)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(filePath), 0o700); err != nil {
		return "", err
	}
	if err := os.WriteFile(filePath, body, 0o600); err != nil {
		return "", err
	}
	return "local://" + key, nil
}

func (s *Local) Get(ctx context.Context, uri string) ([]byte, error) {
	_ = ctx
	key := strings.TrimPrefix(uri, "local://")
	filePath, err := s.safePath(key)
	if err != nil {
		return nil, err
	}
	content, err := os.ReadFile(filePath)
	if err != nil {
		return nil, err
	}
	return content, nil
}

func (s *Local) GetText(ctx context.Context, uri string) (string, error) {
	content, err := s.Get(ctx, uri)
	if err != nil {
		return "", err
	}
	return string(content), nil
}

func (s *Local) Delete(ctx context.Context, uri string) error {
	_ = ctx
	key := strings.TrimPrefix(uri, "local://")
	filePath, err := s.safePath(key)
	if err != nil {
		return err
	}
	if err := os.Remove(filePath); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func (s *Local) Health(ctx context.Context) map[string]any {
	_ = ctx
	return map[string]any{"ok": true, "backend": "local", "root": s.root}
}

func (s *Local) safePath(key string) (string, error) {
	clean := path.Clean("/" + key)
	if clean == "/" || strings.Contains(clean, "..") {
		return "", errors.New("invalid_object_key")
	}
	return filepath.Join(s.root, strings.TrimPrefix(clean, "/")), nil
}

type OSS struct {
	endpoint  string
	bucket    string
	accessKey string
	secretKey string
	prefix    string
	client    *http.Client
}

func (s *OSS) PutText(ctx context.Context, key string, text string, contentType string) (string, error) {
	return s.Put(ctx, key, []byte(text), contentType)
}

func (s *OSS) Put(ctx context.Context, key string, body []byte, contentType string) (string, error) {
	key = s.fullKey(key)
	u := s.urlForKey(key)
	req, err := http.NewRequestWithContext(ctx, http.MethodPut, u, bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	if contentType == "" {
		contentType = "text/plain; charset=utf-8"
	}
	req.Header.Set("Content-Type", contentType)
	s.sign(req, key)
	resp, err := s.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return "", fmt.Errorf("oss_put_failed:%d:%s", resp.StatusCode, string(body))
	}
	return "oss://" + s.bucket + "/" + key, nil
}

func (s *OSS) Get(ctx context.Context, uri string) ([]byte, error) {
	key, err := s.keyFromURI(uri)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.urlForKey(key), nil)
	if err != nil {
		return nil, err
	}
	s.sign(req, key)
	resp, err := s.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("oss_get_failed:%d", resp.StatusCode)
	}
	return io.ReadAll(resp.Body)
}

func (s *OSS) GetText(ctx context.Context, uri string) (string, error) {
	body, err := s.Get(ctx, uri)
	if err != nil {
		return "", err
	}
	return string(body), nil
}

func (s *OSS) Delete(ctx context.Context, uri string) error {
	key, err := s.keyFromURI(uri)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, s.urlForKey(key), nil)
	if err != nil {
		return err
	}
	s.sign(req, key)
	resp, err := s.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 && resp.StatusCode != http.StatusNotFound {
		return fmt.Errorf("oss_delete_failed:%d", resp.StatusCode)
	}
	return nil
}

func (s *OSS) Health(ctx context.Context) map[string]any {
	req, err := http.NewRequestWithContext(ctx, http.MethodHead, s.endpoint+"/"+s.bucket, nil)
	if err == nil {
		s.sign(req, "")
		if resp, err := s.client.Do(req); err == nil {
			defer resp.Body.Close()
			return map[string]any{"ok": resp.StatusCode < 500, "backend": "oss", "bucket": s.bucket, "status": resp.StatusCode}
		}
	}
	return map[string]any{"ok": false, "backend": "oss", "bucket": s.bucket}
}

func (s *OSS) sign(req *http.Request, key string) {
	date := time.Now().UTC().Format(http.TimeFormat)
	req.Header.Set("Date", date)
	contentType := req.Header.Get("Content-Type")
	canonicalResource := "/" + s.bucket
	if key != "" {
		canonicalResource += "/" + key
	}
	stringToSign := req.Method + "\n\n" + contentType + "\n" + date + "\n" + canonicalResource
	mac := hmac.New(sha1.New, []byte(s.secretKey))
	_, _ = mac.Write([]byte(stringToSign))
	signature := base64.StdEncoding.EncodeToString(mac.Sum(nil))
	req.Header.Set("Authorization", "OSS "+s.accessKey+":"+signature)
}

func (s *OSS) urlForKey(key string) string {
	escaped := strings.ReplaceAll(url.PathEscape(key), "%2F", "/")
	return s.endpoint + "/" + s.bucket + "/" + escaped
}

func (s *OSS) fullKey(key string) string {
	key = strings.TrimPrefix(path.Clean("/"+key), "/")
	if s.prefix == "" {
		return key
	}
	return s.prefix + "/" + key
}

func (s *OSS) keyFromURI(uri string) (string, error) {
	uri = strings.TrimPrefix(uri, "oss://")
	prefix := s.bucket + "/"
	if !strings.HasPrefix(uri, prefix) {
		return "", errors.New("object_bucket_mismatch")
	}
	key := strings.TrimPrefix(uri, prefix)
	if key == "" || strings.Contains(key, "..") {
		return "", errors.New("invalid_object_key")
	}
	return key, nil
}

func cleanPrefix(value string) string {
	return strings.Trim(strings.TrimPrefix(path.Clean("/"+value), "/"), "/")
}
