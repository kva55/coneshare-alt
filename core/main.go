package main

import (
	"crypto/rand"
	"crypto/subtle"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/google/uuid"
	"github.com/joho/godotenv"
)

type Config struct {
	StoragePath      string
	InternalAPIToken string
	ServerPort       string
	// MaxUploadBytes caps a single upload body. 0 disables the cap.
	MaxUploadBytes int64
	// EncryptionMode is "required" (default: files are sealed at rest) or "off" (explicit opt-out).
	EncryptionMode string
	EncryptionKey  string
	// Cipher is built from EncryptionKey; nil means files are stored as uploaded.
	Cipher *FileCipher
}

// Published defaults that must never be accepted as the internal token.
var knownPublicTokens = map[string]bool{
	"supersecrettoken": true,
	"changeme":         true,
	"change-me":        true,
	"secret":           true,
}

const minInternalTokenLength = 32
const defaultMaxUploadBytes int64 = 500 * 1024 * 1024 // 500 MiB

// validateConfig refuses to start with a missing, public or weak internal token.
func validateConfig(c Config) error {
	token := strings.TrimSpace(c.InternalAPIToken)
	if token == "" {
		return errors.New("INTERNAL_API_TOKEN must be set (generate one with: openssl rand -hex 32)")
	}
	if knownPublicTokens[token] {
		return errors.New("INTERNAL_API_TOKEN uses a publicly known default value; generate a new one")
	}
	if len(token) < minInternalTokenLength {
		return fmt.Errorf("INTERNAL_API_TOKEN must be at least %d characters", minInternalTokenLength)
	}
	if c.MaxUploadBytes < 0 {
		return errors.New("MAX_UPLOAD_BYTES must not be negative")
	}
	switch c.EncryptionMode {
	case "", "off":
	case "required":
		if _, err := NewFileCipher(c.EncryptionKey); err != nil {
			return fmt.Errorf("storage encryption is required (STORAGE_ENCRYPTION=required): %w", err)
		}
	default:
		return errors.New("STORAGE_ENCRYPTION must be \"required\" or \"off\"")
	}
	return nil
}

type URLRequest struct {
	StorageKey    string `json:"storage_key"`
	Filename      string `json:"filename"`
	ExpirySeconds int    `json:"expiry_seconds,omitempty"`
	// Secure asks delete-file to overwrite the data before unlinking it (data retention hard deletes).
	Secure bool `json:"secure,omitempty"`
}

type URLResponse struct {
	URL string `json:"url"`
}

type CopyFileRequest struct {
	SourceStorageKey      string `json:"source_storage_key"`
	DestinationStorageKey string `json:"destination_storage_key"`
}

type FileInfoResponse struct {
	Exists bool  `json:"exists"`
	Size   int64 `json:"size"`
}

type TokenInfo struct {
	StorageKey string
	Filename   string
	ExpiresAt  time.Time
}

// TODO: The tokenStore is a global in-memory map. This approach has two main drawbacks:
//    Scalability: The service is stateful. If you need to run multiple instances for high availability or load balancing, they won't share the token state, causing requests to fail depending on which instance they hit.
//    Testability: Global state makes parallel testing difficult and can lead to flaky tests.
// Consider encapsulating the token store and its lock within a struct that can be passed to handlers. For production scalability, might eventually need to move this state to a shared store like Redis.
var (
	tokenStore = make(map[string]TokenInfo)
	storeLock  = sync.RWMutex{}
)

func main() {
	err := godotenv.Load()
	if err != nil {
		log.Println("No .env file found, using environment variables")
	}

	config := loadConfig()
	if err := validateConfig(config); err != nil {
		log.Fatalf("Invalid configuration: %v", err)
	}
	if config.EncryptionMode == "required" {
		config.Cipher, _ = NewFileCipher(config.EncryptionKey) // validated above
		log.Printf("Storage encryption at rest: ON (AES-256-GCM)")
	} else {
		log.Printf("WARNING: storage encryption at rest is OFF (STORAGE_ENCRYPTION=off)")
	}
	if len(os.Args) > 1 && os.Args[1] == "-encrypt-existing" {
		if config.Cipher == nil {
			log.Fatal("-encrypt-existing needs STORAGE_ENCRYPTION=required and a key")
		}
		n, err := config.Cipher.EncryptExisting(config.StoragePath)
		log.Printf("Encrypted %d existing file(s)", n)
		if err != nil {
			log.Fatalf("Stopped: %v", err)
		}
		return
	}

	// Start a background goroutine to clean up expired tokens
	go cleanupExpiredTokens()

	r := chi.NewRouter()
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)

	// Internal API routes, protected by a shared token
	r.Route("/internal/v1", func(r chi.Router) {
		r.Use(AuthMiddleware(config.InternalAPIToken))
		r.Post("/generate-upload-url", generateURLHandler("upload", 1*time.Hour))
		r.Post("/generate-download-url", generateURLHandler("download", 1*time.Hour))
		r.Post("/generate-preview-url", generateURLHandler("preview", 5*time.Minute))
		r.Post("/delete-file", deleteFileHandler(config))
		r.Post("/copy-file", copyFileHandler(config))
		r.Post("/file-info", getFileInfoHandler(config))
	})

	// Health check endpoint
	r.Get("/health", healthHandler())

	// Public routes for file handling
	r.Put("/files/upload/{token}", handleUpload(config))
	r.Get("/files/download/{token}", handleDownload(config, "attachment"))
	r.Get("/files/preview/{token}", handleDownload(config, "inline"))
	r.Get("/files/preview/{token}/*", handleDownload(config, "inline"))

	certFile, keyFile := os.Getenv("TLS_CERT_FILE"), os.Getenv("TLS_KEY_FILE")
	if certFile != "" || keyFile != "" {
		if certFile == "" || keyFile == "" {
			log.Fatal("TLS_CERT_FILE and TLS_KEY_FILE must be set together")
		}
		log.Printf("Starting file server with TLS on port %s", config.ServerPort)
		srv := &http.Server{Addr: ":" + config.ServerPort, Handler: r, TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12}}
		if err := srv.ListenAndServeTLS(certFile, keyFile); err != nil {
			log.Fatalf("Failed to start server: %v", err)
		}
		return
	}
	log.Printf("WARNING: file server is listening without TLS (set TLS_CERT_FILE and TLS_KEY_FILE)")
	log.Printf("Starting file server on port %s", config.ServerPort)
	if err := http.ListenAndServe(":"+config.ServerPort, r); err != nil {
		log.Fatalf("Failed to start server: %v", err)
	}
}

func loadConfig() Config {
	maxUpload := defaultMaxUploadBytes
	if raw := strings.TrimSpace(getEnv("MAX_UPLOAD_BYTES", "")); raw != "" {
		parsed, err := strconv.ParseInt(raw, 10, 64)
		if err != nil {
			log.Fatalf("Invalid MAX_UPLOAD_BYTES %q: %v", raw, err)
		}
		maxUpload = parsed
	}
	mode := strings.ToLower(strings.TrimSpace(getEnv("STORAGE_ENCRYPTION", "required")))
	return Config{
		EncryptionMode:   mode,
		EncryptionKey:    getEnv("STORAGE_ENCRYPTION_KEY", ""),
		StoragePath:      getEnv("STORAGE_PATH", "/storage"),
		InternalAPIToken: getEnv("INTERNAL_API_TOKEN", ""),
		ServerPort:       getEnv("PORT", "8080"),
		MaxUploadBytes:   maxUpload,
	}
}

func generateURLHandler(action string, expiryDuration time.Duration) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var reqBody URLRequest
		if err := json.NewDecoder(r.Body).Decode(&reqBody); err != nil {
			http.Error(w, "Invalid request body", http.StatusBadRequest)
			return
		}
		if reqBody.StorageKey == "" {
			http.Error(w, "storage_key is required", http.StatusBadRequest)
			return
		}

		cleanKey := filepath.Clean(reqBody.StorageKey)
		if strings.HasPrefix(cleanKey, "..") || filepath.IsAbs(reqBody.StorageKey) {
			http.Error(w, "Invalid storage_key", http.StatusBadRequest)
			return
		}

		tokenExpiry := expiryDuration
		if reqBody.ExpirySeconds > 0 {
			maxDuration := 24 * time.Hour
			maxSeconds := int(maxDuration / time.Second)
			if reqBody.ExpirySeconds > maxSeconds {
				tokenExpiry = maxDuration
			} else {
				tokenExpiry = time.Duration(reqBody.ExpirySeconds) * time.Second
			}
		} else if action == "preview" && (strings.HasSuffix(cleanKey, "playlist.m3u8") || strings.Contains(cleanKey, "_hls")) {
			// Video HLS streams require longer TTL to support playback beyond 5 minutes
			tokenExpiry = 4 * time.Hour
		}

		token := uuid.New().String()
		expiry := time.Now().Add(tokenExpiry)

		storeLock.Lock()
		tokenStore[token] = TokenInfo{
			StorageKey: reqBody.StorageKey,
			Filename:   reqBody.Filename,
			ExpiresAt:  expiry,
		}
		storeLock.Unlock()

		url := filepath.Join("/files", action, token)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(URLResponse{URL: url})
	}
}

func handleUpload(config Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		token := chi.URLParam(r, "token")

		storeLock.Lock()
		info, ok := tokenStore[token]
		if ok {
			delete(tokenStore, token) // Token is single-use
		}
		storeLock.Unlock()

		if !ok || time.Now().After(info.ExpiresAt) {
			http.Error(w, "Invalid or expired upload token", http.StatusForbidden)
			return
		}

		filePath := filepath.Join(config.StoragePath, info.StorageKey)

		// Security check to prevent path traversal.
		storageRoot, err := filepath.Abs(config.StoragePath)
		if err != nil {
			log.Printf("Configuration error: invalid storage path: %v", err)
			http.Error(w, "Internal server error due to server configuration", http.StatusInternalServerError)
			return
		}
		safePrefix := storageRoot + string(os.PathSeparator)

		cleanPath, err := filepath.Abs(filePath)
		if err != nil {
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		if !strings.HasPrefix(cleanPath, safePrefix) {
			http.Error(w, "Forbidden: path traversal attempt", http.StatusForbidden)
			return
		}

		dir := filepath.Dir(filePath)
		if err := os.MkdirAll(dir, 0755); err != nil {
			log.Printf("Error creating directory %s: %v", dir, err)
			http.Error(w, "Could not create storage directory", http.StatusInternalServerError)
			return
		}

		tmpPath := fmt.Sprintf("%s.part-%s", filePath, uuid.New().String())
		outFile, err := os.Create(tmpPath)
		if err != nil {
			log.Printf("Error creating temp file %s: %v", tmpPath, err)
			http.Error(w, "Could not save file", http.StatusInternalServerError)
			return
		}

		uploadSuccess := false
		defer func() {
			outFile.Close()
			if !uploadSuccess {
				os.Remove(tmpPath)
			}
		}()

		body := r.Body
		if config.MaxUploadBytes > 0 {
			body = http.MaxBytesReader(w, r.Body, config.MaxUploadBytes)
		}
		if config.Cipher != nil {
			sealer := config.Cipher.NewWriter(outFile)
			_, err = io.Copy(sealer, body)
			if err == nil {
				err = sealer.Close()
			}
		} else {
			_, err = io.Copy(outFile, body)
		}
		if err != nil {
			var maxErr *http.MaxBytesError
			if errors.As(err, &maxErr) {
				http.Error(w, "Upload exceeds the maximum allowed size", http.StatusRequestEntityTooLarge)
				return
			}
			log.Printf("Error writing file %s: %v", tmpPath, err)
			http.Error(w, "Failed to write file content", http.StatusInternalServerError)
			return
		}

		if err := outFile.Close(); err != nil {
			log.Printf("Error closing temp file %s: %v", tmpPath, err)
			http.Error(w, "Failed to flush file", http.StatusInternalServerError)
			return
		}

		if err := os.Rename(tmpPath, filePath); err != nil {
			log.Printf("Error renaming file from %s to %s: %v", tmpPath, filePath, err)
			http.Error(w, "Failed to finalize file", http.StatusInternalServerError)
			return
		}
		uploadSuccess = true

		w.WriteHeader(http.StatusOK)
	}
}

func deleteFileHandler(config Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var reqBody URLRequest
		if err := json.NewDecoder(r.Body).Decode(&reqBody); err != nil {
			http.Error(w, "Invalid request body", http.StatusBadRequest)
			return
		}
		if reqBody.StorageKey == "" {
			http.Error(w, "storage_key is required", http.StatusBadRequest)
			return
		}

		filePath := filepath.Join(config.StoragePath, reqBody.StorageKey)

		// Security check to prevent path traversal.
		storageRoot, err := filepath.Abs(config.StoragePath)
		if err != nil {
			log.Printf("Configuration error: invalid storage path: %v", err)
			http.Error(w, "Internal server error due to server configuration", http.StatusInternalServerError)
			return
		}
		safePrefix := storageRoot + string(os.PathSeparator)

		cleanPath, err := filepath.Abs(filePath)
		if err != nil {
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		if !strings.HasPrefix(cleanPath, safePrefix) {
			http.Error(w, "Forbidden: path traversal attempt", http.StatusForbidden)
			return
		}

		fileInfo, err := os.Stat(cleanPath)
		if err != nil {
			if os.IsNotExist(err) {
				// Not an error if file is already gone
				w.WriteHeader(http.StatusNoContent)
				return
			}
			log.Printf("Error stating file %s: %v", cleanPath, err)
			http.Error(w, "Could not delete file", http.StatusInternalServerError)
			return
		}

		if reqBody.Secure {
			// Fail closed: if the data cannot be overwritten the file is kept, so the caller keeps its
			// record and retries, instead of believing the data is gone.
			if oerr := overwriteTree(cleanPath); oerr != nil {
				log.Printf("Error overwriting %s before delete: %v", cleanPath, oerr)
				http.Error(w, "Could not securely delete file", http.StatusInternalServerError)
				return
			}
		}

		if fileInfo.IsDir() {
			baseName := filepath.Base(cleanPath)
			if strings.HasSuffix(baseName, "_hls") {
				err = os.RemoveAll(cleanPath)
			} else {
				err = os.Remove(cleanPath)
			}
		} else {
			err = os.Remove(cleanPath)
		}

		if err != nil {
			if os.IsNotExist(err) {
				// Not an error if file is already gone
				w.WriteHeader(http.StatusNoContent)
				return
			}
			log.Printf("Error deleting file %s: %v", cleanPath, err)
			http.Error(w, "Could not delete file", http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusNoContent)
	}
}

// overwriteFile replaces the contents of a regular file, in place, with one pass of random bytes and
// flushes it to disk. This is the "Clear" technique of NIST SP 800-88 Rev. 1 for logical media.
// It does not reach copies the storage layer keeps elsewhere (SSD wear levelling, copy-on-write or
// journalling file systems, snapshots, backups); see docs/fork/data-retention.md.
func overwriteFile(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return nil // never follow symbolic links or touch devices
	}
	f, err := os.OpenFile(path, os.O_WRONLY, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	buf := make([]byte, 1<<20)
	remaining := info.Size()
	for remaining > 0 {
		n := int64(len(buf))
		if remaining < n {
			n = remaining
		}
		if _, err := rand.Read(buf[:n]); err != nil {
			return err
		}
		if _, err := f.Write(buf[:n]); err != nil {
			return err
		}
		remaining -= n
	}
	if err := f.Sync(); err != nil {
		return err
	}
	return f.Truncate(0)
}

// overwriteTree overwrites a file, or every regular file below a directory.
func overwriteTree(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.IsDir() {
		return overwriteFile(path)
	}
	return filepath.Walk(path, func(p string, fi os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if fi.Mode().IsRegular() {
			return overwriteFile(p)
		}
		return nil
	})
}

func getFileInfoHandler(config Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var reqBody URLRequest
		if err := json.NewDecoder(r.Body).Decode(&reqBody); err != nil {
			http.Error(w, "Invalid request body", http.StatusBadRequest)
			return
		}
		if reqBody.StorageKey == "" {
			http.Error(w, "storage_key is required", http.StatusBadRequest)
			return
		}

		filePath := filepath.Join(config.StoragePath, reqBody.StorageKey)

		// Security check to prevent path traversal.
		storageRoot, err := filepath.Abs(config.StoragePath)
		if err != nil {
			log.Printf("Configuration error: invalid storage path: %v", err)
			http.Error(w, "Internal server error due to server configuration", http.StatusInternalServerError)
			return
		}
		safePrefix := storageRoot + string(os.PathSeparator)

		cleanPath, err := filepath.Abs(filePath)
		if err != nil {
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		if !strings.HasPrefix(cleanPath, safePrefix) {
			http.Error(w, "Forbidden: path traversal attempt", http.StatusForbidden)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		info, err := os.Stat(cleanPath)
		if err != nil {
			if os.IsNotExist(err) {
				json.NewEncoder(w).Encode(FileInfoResponse{Exists: false, Size: 0})
				return
			}
			log.Printf("Error checking file info for %s: %v", cleanPath, err)
			http.Error(w, "Could not check file info", http.StatusInternalServerError)
			return
		}

		if info.IsDir() {
			json.NewEncoder(w).Encode(FileInfoResponse{Exists: false, Size: 0})
			return
		}

		json.NewEncoder(w).Encode(FileInfoResponse{
			Exists: true,
			Size:   config.Cipher.storedPlainSize(cleanPath, info.Size()),
		})
	}
}

func copyFileHandler(config Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var reqBody CopyFileRequest
		if err := json.NewDecoder(r.Body).Decode(&reqBody); err != nil {
			http.Error(w, "Invalid request body", http.StatusBadRequest)
			return
		}
		if reqBody.SourceStorageKey == "" || reqBody.DestinationStorageKey == "" {
			http.Error(w, "source_storage_key and destination_storage_key are required", http.StatusBadRequest)
			return
		}

		sourcePath := filepath.Join(config.StoragePath, reqBody.SourceStorageKey)
		destPath := filepath.Join(config.StoragePath, reqBody.DestinationStorageKey)

		// Security check to prevent path traversal.
		storageRoot, err := filepath.Abs(config.StoragePath)
		if err != nil {
			log.Printf("Configuration error: invalid storage path: %v", err)
			http.Error(w, "Internal server error due to server configuration", http.StatusInternalServerError)
			return
		}
		safePrefix := storageRoot + string(os.PathSeparator)

		cleanSourcePath, err := filepath.Abs(sourcePath)
		if err != nil {
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		cleanDestPath, err := filepath.Abs(destPath)
		if err != nil {
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		if !strings.HasPrefix(cleanSourcePath, safePrefix) || !strings.HasPrefix(cleanDestPath, safePrefix) {
			http.Error(w, "Forbidden: path traversal attempt", http.StatusForbidden)
			return
		}

		sourceFile, err := os.Open(sourcePath)
		if err != nil {
			if os.IsNotExist(err) {
				http.Error(w, "Source file not found", http.StatusNotFound)
				return
			}
			log.Printf("Error opening source file %s: %v", sourcePath, err)
			http.Error(w, "Could not open source file", http.StatusInternalServerError)
			return
		}
		defer sourceFile.Close()

		destDir := filepath.Dir(destPath)
		if err := os.MkdirAll(destDir, 0755); err != nil {
			log.Printf("Error creating destination directory %s: %v", destDir, err)
			http.Error(w, "Could not create storage directory", http.StatusInternalServerError)
			return
		}

		destFile, err := os.Create(destPath)
		if err != nil {
			log.Printf("Error creating destination file %s: %v", destPath, err)
			http.Error(w, "Could not create destination file", http.StatusInternalServerError)
			return
		}
		defer destFile.Close()

		_, err = io.Copy(destFile, sourceFile)
		if err != nil {
			log.Printf("Error copying file from %s to %s: %v", sourcePath, destPath, err)
			http.Error(w, "Failed to copy file content", http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusCreated)
	}
}

var dangerousInlineExtensions = map[string]bool{
	".html":  true,
	".htm":   true,
	".xhtml": true,
	".svg":   true,
	".xml":   true,
	".js":    true,
	".mjs":   true,
	".php":   true,
	".sh":    true,
}

func handleDownload(config Config, disposition string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		token := chi.URLParam(r, "token")

		storeLock.RLock()
		info, ok := tokenStore[token]
		storeLock.RUnlock()

		if !ok || time.Now().After(info.ExpiresAt) {
			http.Error(w, "Invalid or expired download link", http.StatusForbidden)
			return
		}

		cleanKey := filepath.Clean(info.StorageKey)
		if strings.HasPrefix(cleanKey, "..") || filepath.IsAbs(info.StorageKey) {
			http.Error(w, "Forbidden: path traversal attempt", http.StatusForbidden)
			return
		}

		subpath := chi.URLParam(r, "*")
		var filePath string
		if subpath != "" {
			// Subpath access is only permitted for HLS playlists located in dedicated *_hls directories.
			// The playlist.m3u8 convention is enforced by generate_video_stream_task in tasks.py.
			parentDir := filepath.Base(filepath.Dir(cleanKey))
			fileName := filepath.Base(cleanKey)
			if fileName != "playlist.m3u8" || !strings.HasSuffix(parentDir, "_hls") {
				http.Error(w, "Forbidden: subpath access not permitted for this token", http.StatusForbidden)
				return
			}

			dir := filepath.Dir(cleanKey)
			filePath = filepath.Join(config.StoragePath, dir, subpath)
		} else {
			filePath = filepath.Join(config.StoragePath, cleanKey)
		}

		// Security check 1: prevent path traversal outside of the storage root.
		storageRoot, err := filepath.Abs(config.StoragePath)
		if err != nil {
			log.Printf("Configuration error: invalid storage path: %v", err)
			http.Error(w, "Internal server error due to server configuration", http.StatusInternalServerError)
			return
		}
		safeRootPrefix := storageRoot + string(os.PathSeparator)

		cleanPath, err := filepath.Abs(filePath)
		if err != nil {
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		if !strings.HasPrefix(cleanPath, safeRootPrefix) {
			http.Error(w, "Forbidden: path traversal attempt", http.StatusForbidden)
			return
		}

		// Security check 2: prevent path traversal outside of the authorized directory.
		authorizedDir := filepath.Join(config.StoragePath, filepath.Dir(info.StorageKey))
		authorizedDirAbs, err := filepath.Abs(authorizedDir)
		if err != nil {
			log.Printf("Configuration error: invalid storage path: %v", err)
			http.Error(w, "Internal server error due to server configuration", http.StatusInternalServerError)
			return
		}
		safeAuthorizedPrefix := authorizedDirAbs + string(os.PathSeparator)
		if !strings.HasPrefix(cleanPath, safeAuthorizedPrefix) {
			http.Error(w, "Forbidden: path traversal attempt", http.StatusForbidden)
			return
		}


		var downloadName string
		if info.Filename != "" {
			downloadName = filepath.Base(info.Filename)
		} else {
			downloadName = filepath.Base(filePath)
		}

		downloadExt := strings.ToLower(filepath.Ext(downloadName))
		fileExt := strings.ToLower(filepath.Ext(filePath))

		effectiveDisposition := disposition
		if effectiveDisposition == "inline" && (dangerousInlineExtensions[downloadExt] || dangerousInlineExtensions[fileExt]) {
			effectiveDisposition = "attachment"
		}

		setNoStoreHeaders(w)
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if effectiveDisposition == "inline" {
			w.Header().Set("Content-Security-Policy", "default-src 'none'; sandbox")
		}

		safeName := url.PathEscape(downloadName)
		w.Header().Set("Content-Disposition",
			fmt.Sprintf(`%s; filename*=UTF-8''%s`, effectiveDisposition, safeName))
		if config.Cipher == nil {
			http.ServeFile(w, r, filePath)
			return
		}
		serveDecrypted(w, r, config.Cipher, filePath)
	}
}

func cleanupExpiredTokens() {
	ticker := time.NewTicker(10 * time.Minute)
	defer ticker.Stop()

	for range ticker.C {
		storeLock.Lock()
		for token, info := range tokenStore {
			if time.Now().After(info.ExpiresAt) {
				delete(tokenStore, token)
			}
		}
		storeLock.Unlock()
	}
}

func AuthMiddleware(token string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
if token == "" {
	log.Println("Critical: INTERNAL_API_TOKEN is not set. Auth is required and is being enforced.")
	http.Error(w, "Forbidden", http.StatusForbidden)
	return
}
			authHeader := r.Header.Get("Authorization")
			expected := "Bearer " + token
			if subtle.ConstantTimeCompare([]byte(authHeader), []byte(expected)) != 1 {
				http.Error(w, "Forbidden", http.StatusForbidden)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func healthHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	}
}

func getEnv(key, fallback string) string {
	if value, ok := os.LookupEnv(key); ok {
		return value
	}
	return fallback
}

// setNoStoreHeaders keeps a document out of browser and proxy caches, and out of the Referer of anything it
// links to. (Last-Modified may still be sent by the standard library; with no-store it cannot be used to cache.)
func setNoStoreHeaders(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "no-store, private, max-age=0")
	w.Header().Set("Pragma", "no-cache")
	w.Header().Set("Expires", "0")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("Cross-Origin-Resource-Policy", "same-origin")
}

// serveDecrypted answers a download from a stored file, decrypting it on the fly. Range requests work.
func serveDecrypted(w http.ResponseWriter, r *http.Request, c *FileCipher, path string) {
	st, err := os.Stat(path)
	if err != nil || st.IsDir() {
		http.NotFound(w, r)
		return
	}
	rs, _, err := c.Open(path)
	if err != nil {
		log.Printf("Could not open %s: %v", path, err)
		http.Error(w, "Could not read file", http.StatusInternalServerError)
		return
	}
	defer rs.Close()
	http.ServeContent(w, r, filepath.Base(path), st.ModTime(), rs)
}
