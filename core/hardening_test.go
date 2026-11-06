package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
)

func TestValidateConfigRejectsMissingPublicAndWeakTokens(t *testing.T) {
	cases := []struct {
		name    string
		token   string
		wantErr string
	}{
		{"missing", "", "must be set"},
		{"whitespace", "   ", "must be set"},
		{"published default", "supersecrettoken", "publicly known"},
		{"too short", "abc123", "at least"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := validateConfig(Config{InternalAPIToken: tc.token})
			if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
				t.Fatalf("expected error containing %q, got %v", tc.wantErr, err)
			}
		})
	}

	if err := validateConfig(Config{InternalAPIToken: strings.Repeat("a", minInternalTokenLength)}); err != nil {
		t.Fatalf("expected a %d-char token to be accepted, got %v", minInternalTokenLength, err)
	}
	if err := validateConfig(Config{InternalAPIToken: strings.Repeat("a", 64), MaxUploadBytes: -1}); err == nil {
		t.Fatalf("expected negative MAX_UPLOAD_BYTES to be rejected")
	}
}

func TestAuthMiddlewareRequiresExactBearerToken(t *testing.T) {
	token := strings.Repeat("t", 40)
	handler := AuthMiddleware(token)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	for header, want := range map[string]int{
		"":                         http.StatusForbidden,
		"Bearer wrong":             http.StatusForbidden,
		"Bearer " + token + "x":    http.StatusForbidden,
		"bearer " + token:          http.StatusForbidden,
		"Bearer " + token:          http.StatusNoContent,
	} {
		req := httptest.NewRequest("POST", "/internal/v1/file-info", nil)
		if header != "" {
			req.Header.Set("Authorization", header)
		}
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != want {
			t.Errorf("Authorization %q: expected %d, got %d", header, want, rec.Code)
		}
	}
}

func TestHandleUploadRejectsBodiesOverTheCap(t *testing.T) {
	tempDir := t.TempDir()
	config := Config{StoragePath: tempDir, MaxUploadBytes: 16}

	r := chi.NewRouter()
	r.Put("/files/upload/{token}", handleUpload(config))

	register := func(token, key string) {
		storeLock.Lock()
		tokenStore[token] = TokenInfo{StorageKey: key, ExpiresAt: time.Now().Add(time.Hour)}
		storeLock.Unlock()
	}

	// Over the cap: 413 and nothing left on disk.
	register("token-too-big", "org/too-big.pdf")
	req := httptest.NewRequest("PUT", "/files/upload/token-too-big", strings.NewReader(strings.Repeat("x", 17)))
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("expected 413, got %d", rec.Code)
	}
	if _, err := os.Stat(filepath.Join(tempDir, "org/too-big.pdf")); !os.IsNotExist(err) {
		t.Fatalf("oversized upload must not be stored")
	}
	entries, _ := os.ReadDir(filepath.Join(tempDir, "org"))
	for _, e := range entries {
		if strings.Contains(e.Name(), ".part-") {
			t.Fatalf("temporary part file left behind: %s", e.Name())
		}
	}

	// Exactly at the cap: accepted.
	register("token-ok", "org/ok.pdf")
	req = httptest.NewRequest("PUT", "/files/upload/token-ok", strings.NewReader(strings.Repeat("y", 16)))
	rec = httptest.NewRecorder()
	r.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 at the cap, got %d", rec.Code)
	}
}
