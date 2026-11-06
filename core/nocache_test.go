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

func TestDownloadAndPreviewAreNeverCacheable(t *testing.T) {
	dir := t.TempDir()
	key := "uploads/doc.txt"
	full := filepath.Join(dir, key)
	_ = os.MkdirAll(filepath.Dir(full), 0o755)
	_ = os.WriteFile(full, []byte("hello"), 0o600)
	config := Config{StoragePath: dir}

	r := chi.NewRouter()
	r.Get("/files/download/{token}", handleDownload(config, "attachment"))
	r.Get("/files/preview/{token}", handleDownload(config, "inline"))

	for _, route := range []string{"download", "preview"} {
		token := "nocache-" + route
		tokenStore[token] = TokenInfo{StorageKey: key, Filename: "doc.txt", ExpiresAt: time.Now().Add(time.Hour)}
		rec := httptest.NewRecorder()
		r.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/files/"+route+"/"+token, nil))
		if rec.Code != http.StatusOK {
			t.Fatalf("%s: status %d", route, rec.Code)
		}
		cc := rec.Header().Get("Cache-Control")
		if !strings.Contains(cc, "no-store") || !strings.Contains(cc, "private") {
			t.Fatalf("%s: Cache-Control = %q", route, cc)
		}
		if rec.Header().Get("Referrer-Policy") != "no-referrer" {
			t.Fatalf("%s: missing Referrer-Policy", route)
		}
	}
}
